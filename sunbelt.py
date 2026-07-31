"""
Sunbelt Network (sunbeltnetwork.com) — Laundromats & Coin Laundry category.

Sunbelt is a national/international general business-brokerage franchise
network; this scraper targets only their "Laundromats & Coin Laundry
Businesses For Sale" category page, which aggregates active listings from
Sunbelt's own regional offices (Sunbelt of Baton Rouge, Sunbelt of Charleston,
etc.) on Sunbelt's own domain — not a third-party aggregator.

NOTE: sunbeltnetwork.com sits behind bot-mitigation (Cloudflare) that returns
HTTP 403 to plain requests/curl traffic, even though the same pages load fine
in a real browser. Verified manually via a real browser session on
2026-07-30: exactly 2 active listings (Baton Rouge, LA and a coastal SC
listing). Those are seeded in output/sunbelt_raw.csv as the last-good dataset.
This scraper makes a genuine best-effort fetch attempt on every run (in case
the runner IP isn't challenged) but if it gets blocked and returns 0 rows,
run_all.py's resilience pattern restores the last-good CSV rather than wiping
real inventory — the same behavior used for any transiently-blocked source.

Source: https://www.sunbeltnetwork.com/business-search/business-results/s-laundromats-coin-laundry-for-sale-128/
Output: output/sunbelt_raw.csv
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils import get_session, polite_delay, parse_price, clean_text

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sunbelt")

BASE_URL = "https://www.sunbeltnetwork.com"
LISTINGS_URL = (
    "{}/business-search/business-results/"
    "s-laundromats-coin-laundry-for-sale-128/".format(BASE_URL)
)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "sunbelt_raw.csv")

FIELDNAMES = [
    "source_id", "title", "city", "state", "asking_price", "annual_revenue",
    "practice_type", "description", "broker_name", "broker_company",
    "listing_url", "machine_count", "listing_code",
]

DETAIL_RE = re.compile(r"/buy-a-business/listings/listing-details/[a-z0-9-]+/?$", re.I)


def infer_type(text: str) -> str:
    t = text.lower()
    if "wash-dry-fold" in t or "wash dry fold" in t or "pickup" in t or "delivery" in t:
        return "Wash-Dry-Fold"
    if "attendant" in t or "attended" in t:
        return "Attended Full-Service"
    if "no on-site attendant" in t or "no attendant" in t or "coin" in t:
        return "Coin-Op"
    if "card" in t:
        return "Card/App-Based"
    return "Coin-Op"


def collect_detail_urls(session) -> List[str]:
    polite_delay(1.0, 2.0)
    try:
        resp = session.get(LISTINGS_URL, timeout=30)
        if resp.status_code != 200:
            logger.warning("Index page -> HTTP %d (likely bot-mitigation); "
                            "0 rows this run, last-good CSV will be restored "
                            "by run_all.py", resp.status_code)
            return []
    except Exception as e:
        logger.warning("Index page failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0]
        if not DETAIL_RE.search(href):
            continue
        full = href if href.startswith("http") else BASE_URL + href
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
            return None
    except Exception as e:
        logger.warning("Detail failed %s: %s", url, e)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    h1 = soup.find("h1")
    title = clean_text(h1.get_text()) if h1 else ""
    if not title:
        return None

    full_text = soup.get_text(" ", strip=True)

    price = None
    m = re.search(r"Asking Price\D{0,20}\$?([\d,]{4,})", full_text, re.I)
    if m:
        price = parse_price(m.group(1))

    revenue = None
    m = re.search(r"Gross Revenue\D{0,20}\$?([\d,]{4,})", full_text, re.I)
    if m:
        revenue = parse_price(m.group(1))

    city = ""
    state = ""
    m = re.search(r"City:\s*([A-Za-z .'-]+?)\s+State:\s*([A-Za-z ]+)", full_text)
    if m:
        c = m.group(1).strip()
        city = "" if c.lower() == "confidential" else c
        state = m.group(2).strip()

    # The "Business Listed by" block is tightly bounded (agent name, then
    # office name, then "Email <FirstName>") — anchor on both ends so this
    # doesn't run on into unrelated body copy.
    broker_name = ""
    broker_office = ""
    m = re.search(
        r"Business Listed by\s+([A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+){0,2}?)\s+"
        r"(Sunbelt[a-zA-Z .'-]{2,40}?)\s+Email\b",
        full_text,
    )
    if m:
        broker_name = m.group(1).strip()
        broker_office = m.group(2).strip()

    description = ""
    for p in soup.find_all("p"):
        t = clean_text(p.get_text(" ", strip=True))
        if len(t) >= 60:
            description = t[:600]
            break

    slug = url.rstrip("/").rsplit("/", 1)[-1]

    return {
        "source_id": "sun-{}".format(slug),
        "title": title,
        "city": city,
        "state": state,
        "asking_price": price,
        "annual_revenue": revenue,
        "practice_type": infer_type(title + " " + description),
        "description": description,
        "broker_name": broker_name or broker_office or "Sunbelt Network",
        "broker_company": broker_office or "Sunbelt Network",
        "listing_url": url,
        "machine_count": None,
        "listing_code": slug[:40],
    }


def run() -> List[Dict]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = get_session()
    logger.info("Collecting Sunbelt laundromat detail URLs...")
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
