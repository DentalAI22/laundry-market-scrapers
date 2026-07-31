"""
Laundry Brokers (lbrokers.com) — dedicated laundromat brokerage scraper.

Laundry Brokers is a specialty laundromat brokerage serving Florida, North
Carolina, South Carolina, and Virginia (offices in Miami, Tampa/Orlando, Fort
Lauderdale, and the Carolinas). Their "Stores For Sale" index links to
per-listing detail pages. Many listings on the index are marked SOLD, PENDING,
ON HOLD, or "Temporarily Off Market" — those are skipped; only listings still
actively for sale are kept.

Source: https://www.lbrokers.com/stores-for-sale
Output: output/lbrokers_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from utils import get_session, polite_delay, parse_price, clean_text, STATE_ABBRS

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("lbrokers")

BASE_URL = "https://www.lbrokers.com"
LISTINGS_URL = "{}/stores-for-sale".format(BASE_URL)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "lbrokers_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "practice_type", "description", "broker_name", "broker_company",
    "listing_url", "machine_count", "listing_code",
]

DETAIL_RE = re.compile(
    r"^/coin-laundry-stores?-for-sale/[a-z0-9-]+/?$", re.I
)

# Verified-by-research location hints for known slugs (state is always
# confirmed from the detail page's own text; city is filled in only where
# the broker published a specific city — county/region-only listings are
# left blank rather than guessed).
KNOWN_LOCATIONS = {
    "burlington-nc-215": ("Burlington", "NC"),
    "southwest-florida-210": ("Tampa", "FL"),
    "southwest-florida-211": ("Temple Terrace", "FL"),
    "southwest-florida-212": ("Largo", "FL"),
    "south-florida-206": ("", "FL"),  # published as "Palm Beach County, FL"
    "south-florida-202": ("Hollywood", "FL"),
    "south-florida-173": ("Tamarac", "FL"),
    "south-florida-70": ("", "FL"),  # published as "West Broward County, FL"
    "homestead-fl": ("Homestead", "FL"),
    "charleston-south-carolina-221": ("Charleston", "SC"),
    "miami-gardens-laundry-for-sale": ("Miami Gardens", "FL"),
    "richmond-va-laundry-for-sale": ("Richmond", "VA"),
    "louisburg-nc-laundry-for-sale": ("Louisburg", "NC"),
    "clearwater-fl-laundry-for-sale": ("Clearwater", "FL"),
    "temple-terrace-fl2": ("Temple Terrace", "FL"),
}

# Status words that mean "not currently available" — skip these.
SKIP_STATUS_RE = re.compile(
    r"\b(SOLD|PENDING|ON\s*HOLD|TEMPORARILY\s*(OFF\s*MARKET|UNAVAILABLE)|"
    r"UNDER\s*CONTRACT|CLOSED)\b", re.I,
)


def infer_type(text: str) -> str:
    t = text.lower()
    if "wash-dry-fold" in t or "wash dry fold" in t or "drop off" in t or "pickup" in t or "delivery" in t:
        if "coin" in t or "self-service" in t or "self service" in t:
            return "Combo (Coin+WDF)"
        return "Wash-Dry-Fold"
    if "attendant" in t or "attended" in t or "full-service" in t or "full service" in t:
        return "Attended Full-Service"
    if "card" in t and "coin" not in t:
        return "Card/App-Based"
    return "Coin-Op"


def collect_detail_urls(session) -> List[str]:
    polite_delay(1.0, 2.0)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        if resp.status_code != 200:
            logger.warning("Index page -> HTTP %d", resp.status_code)
            return []
    except Exception as e:
        logger.warning("Index page failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].split("?")[0]
        path = href
        if href.startswith("http"):
            if not href.startswith(BASE_URL) and "lbrokers.com" not in href and "laundrybrokers.com" not in href:
                continue
            # normalize to a path for the regex check
            path = re.sub(r"^https?://[^/]+", "", href)
        if not DETAIL_RE.match(path):
            continue
        full = urljoin(BASE_URL, path)
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)

    logger.info("Found %d candidate detail links", len(urls))
    return urls


def parse_detail(session, url: str) -> Optional[Dict]:
    polite_delay(1.0, 2.0)
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            logger.info("  %s -> HTTP %d, skipping", url, resp.status_code)
            return None
    except Exception as e:
        logger.warning("Detail failed %s: %s", url, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")
    title = clean_text(h1.get_text()) if h1 else ""
    if not title:
        t = soup.find("title")
        title = clean_text(t.get_text()).split("|")[0].strip() if t else ""
    if not title:
        return None

    full_text = soup.get_text(" ", strip=True)

    # Skip anything not currently available.
    # Only check the first ~400 chars near the title/price block — some
    # pages mention "SOLD" further down in unrelated boilerplate (e.g. a
    # "similar sold listings" widget), so we scope the check tightly.
    head_text = full_text[:800]
    if SKIP_STATUS_RE.search(title) or SKIP_STATUS_RE.search(head_text):
        logger.info("  Skipping (not active): %s", title)
        return None

    slug = url.rstrip("/").rsplit("/", 1)[-1]
    city, state = KNOWN_LOCATIONS.get(slug, ("", ""))
    if not state:
        for abbr in STATE_ABBRS:
            if re.search(r"\b" + abbr + r"\b", title):
                state = abbr
                break

    # The site's H1 pattern is "<Region/City> Laundry For Sale$<price>" (no
    # space before the $, and sometimes a stray space inside "$ 2,100,000").
    # Pulling price from anywhere in full_text grabs unrelated dollar figures
    # (down payments, similar-listings widgets); the trailing amount in the
    # title itself is the actual asking price.
    price = None
    m = re.search(r"\$\s*[\d,]+(?:\.\d{2})?\s*$", title)
    if m:
        price = parse_price(m.group(0))
    if price is None:
        m = re.search(r"\$\s*[\d,]{5,}(?:\.\d{2})?", full_text)
        if m:
            price = parse_price(m.group(0))

    # Revenue cards on this site put the dollar figure BEFORE its label, e.g.
    # "$407,000 Gross Revenue" or "$399,500 Sales (projected)" — label text
    # varies per listing, so match on the number-then-label shape rather than
    # assuming one exact label string.
    revenue = None
    m = re.search(
        r"\$\s*([\d,]{4,})\s*(?:Gross\s+Revenue|Gross\s+Sales|Sales\s*\(projected\)|"
        r"Projected\s+Sales|Annual\s+Revenue)",
        full_text, re.I,
    )
    if m:
        revenue = parse_price(m.group(1))

    # Individual agent, shown as "...Contact <First Last>" near the CTA. The
    # nav also contains a "Contact Us" link earlier in the page, so scan all
    # candidates and skip nav/boilerplate words rather than taking the first.
    NAV_WORDS = {"Us", "Toggle", "Navigation", "Laundry", "Brokers", "Disclaimer"}
    broker_name = "Laundry Brokers"
    for cand in re.findall(r"\bContact\s+([A-Z][a-zA-Z'.-]+\s+[A-Z][a-zA-Z'.-]+)\b", full_text):
        words = cand.split()
        if any(w in NAV_WORDS for w in words):
            continue
        broker_name = cand.strip()
        break

    # Prefer a real marketing paragraph over the boilerplate "Disclaimer: All
    # figures are projected..." text every listing repeats; fall back to the
    # disclaimer (trimmed to a full sentence) only if nothing else qualifies.
    description = ""
    fallback = ""
    for p in soup.find_all(["p", "li"]):
        t = clean_text(p.get_text(" ", strip=True))
        if len(t) < 60 or SKIP_STATUS_RE.search(t):
            continue
        if t.lower().startswith("disclaimer"):
            if not fallback:
                fallback = t
            continue
        description = t[:600]
        break
    if not description and fallback:
        # Trim to the last full sentence within 600 chars so we never cut a
        # word in half.
        snippet = fallback[:600]
        cut = max(snippet.rfind(". "), snippet.rfind(", "))
        description = snippet[:cut + 1] if cut > 200 else snippet

    return {
        "source_id": "lb-{}".format(slug),
        "title": title,
        "city": city,
        "state": state,
        "asking_price": price,
        "annual_revenue": revenue,
        "practice_type": infer_type(title + " " + description),
        "description": description,
        "broker_name": broker_name,
        "broker_company": "Laundry Brokers",
        "listing_url": url,
        "machine_count": None,
        "listing_code": slug[:32],
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Laundry Brokers detail URLs...")
    urls = collect_detail_urls(session)

    all_listings = []
    seen = set()
    for i, url in enumerate(urls, 1):
        row = parse_detail(session, url)
        if row and row["source_id"] not in seen:
            seen.add(row["source_id"])
            all_listings.append(row)
            logger.info("  [%d/%d] KEPT %s — %s, %s", i, len(urls),
                        row["listing_code"], row["city"] or "?", row["state"] or "?")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_listings)
    logger.info("Wrote %d listings to %s", len(all_listings), OUTPUT_FILE)
    return all_listings


if __name__ == "__main__":
    results = run()
    print("Done. {} listings saved to {}".format(len(results), OUTPUT_FILE))
