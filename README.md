# laundry-market-scrapers

Public scraper rig for **The Laundry Market** network vertical. Aggregates
real, public-source laundromat-for-sale listings from dedicated laundromat
brokerages and general business brokerages' laundromat categories, and
publishes a single canonical `listings.json` that the live site consumes.

**Live site fed by this repo:** https://thelaundrymarket.com (Vercel project `laundry`)

Everything in this repo is scraper code + public listing data. **No secrets, no
tokens, no seller PII.** Broker contact details that appear on public broker
listing pages are public business contact info.

## What it does

```
run_all.py  ->  per-source scrapers (lbrokers, sunbelt)  ->  output/*_raw.csv
             ->  normalizer.py  ->  listings.json  (canonical, TLM-XXXXX siteIds, deduped)
```

- `utils.py` — real UA + polite 1.0–2.0s delays + price/state helpers.
- `broker_codes.json` — source registry, `site_prefix = TLM`.
- `site_id_registry.json` — persistent TLM- id map. **Never renumber.**
- `listings.json` — the canonical dataset. Tracked on purpose; a re-run
  regenerates and commits it back here.

## Sources

| module    | broker                              | own site                                                                                   | notes |
|-----------|--------------------------------------|----------------------------------------------------------------------------------------------|-------|
| `lbrokers`| Laundry Brokers (FL/NC/SC/VA)         | https://www.lbrokers.com/stores-for-sale                                                     | Specialty laundromat brokerage. Index → per-listing detail pages; SOLD/PENDING/ON HOLD/off-market listings are skipped, only active listings are kept. |
| `sunbelt` | Sunbelt Network — Laundromats & Coin Laundry category | https://www.sunbeltnetwork.com/business-search/business-results/s-laundromats-coin-laundry-for-sale-128/ | Sunbelt's own domain (not a 3rd-party aggregator) — a general brokerage franchise network with a laundromat category, listings attributed to the specific Sunbelt regional office (e.g. "Sunbelt of Baton Rouge"). Sits behind Cloudflare bot-mitigation that 403s plain `requests`/`curl` traffic even though the same pages load fine in a real browser — see the note in `sunbelt.py`. |

**No minimum listing count.** Owner directive: ship with 1, 2, or 0 listings
from a source if that's honestly all that's currently for sale — never pad to
hit a round number. If a source has zero active laundromat listings on a given
day, its scraper legitimately writes 0 rows for that run.

**Never scraped** (blocked aggregators — third-party marketplaces, not a
broker's own site): BizBuySell, BizQuest, LoopNet, DealStream,
BusinessBroker.net, VestedBB, GlobalBX, BizScout, BizBen, SudsList.

## Auto-refresh pipeline (refresh -> live)

`.github/workflows/scrape-laundry.yml` runs **daily at 10:15 UTC** (staggered
after car wash's 10:00 slot to spread GitHub Actions load across the network),
plus manual `workflow_dispatch`. This repo is **PUBLIC**, so GitHub Actions
minutes are unlimited/free.

The Action is **self-contained — it only ever writes to THIS repo:**

1. checkout -> install deps -> `python run_all.py` (scrape + normalize).
2. **Resilience guard (inside `run_all.py`, not a separate step):** if any
   individual source scraper returns 0 rows on a run where it previously had
   real listings (e.g. Sunbelt's Cloudflare block, or a broker's site being
   briefly down), the last-good CSV for THAT SOURCE ONLY is restored so a
   transient block never wipes real inventory. Unlike some sibling verticals,
   there's no network-wide minimum-count floor — a single-source vertical
   legitimately shrinking to a smaller (but still real) count is not treated
   as a failure.
3. commit `listings.json` + `output/*.csv` + `site_id_registry.json` back to
   this repo using the default `GITHUB_TOKEN` (`permissions: contents:
   write`). No PAT.

**Why no cross-repo push:** the site repo (`~/market-network/laundry`,
`DentalAI22/laundry`) is a SEPARATE git repo. Instead of this Action reaching
into it, the site **pulls `listings.json` from this repo's public raw URL at
build time** (`npm` `prebuild` -> `scripts/fetch-listings.mjs` -> the raw URL
below), so a fresh `vercel --prod` / rebuild republishes with the latest
dataset. No cross-repo push credentials required anywhere.

```
https://raw.githubusercontent.com/DentalAI22/laundry-market-scrapers/main/listings.json
```

## Re-run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_all.py                 # scrape all sources + normalize -> listings.json
python run_all.py --only lbrokers # one source
python run_all.py --normalize     # re-normalize existing CSVs (no network)
```

## Constraints honored

- Read-only against public broker pages only; real browser UA; 1.0–2.0s delays.
- Blocked aggregators (see above) are **never** scraped.
- Honest counts; deduped; no fabricated data. Every listing keeps its
  broker's own asking price only — this rig never computes or publishes an
  opinion of value.
