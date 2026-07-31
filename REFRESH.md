# Refreshing The Laundry Market's data

The daily GitHub Action (`.github/workflows/scrape-laundry.yml`, 10:15 UTC)
does this automatically. Use this doc to run it manually — e.g. right after
adding a new broker source, or to force a same-day refresh.

## 1. Scrape + normalize (in this repo)

```bash
cd ~/market-network/laundry-scrapers
python3 -m venv .venv && source .venv/bin/activate   # first time only
pip install -r requirements.txt                       # first time only
python run_all.py
```

This writes `output/<source>_raw.csv` per scraper, then `listings.json`
(canonical dataset, TLM-XXXXX siteIds, deduped). A source that returns 0 rows
(e.g. Sunbelt's Cloudflare bot-mitigation blocking the run) automatically
falls back to its last-good CSV — real inventory is never wiped by a
transient block.

## 2. Commit + push the refreshed dataset

```bash
git add listings.json output/*.csv site_id_registry.json
git commit -m "chore(laundry): refresh listings $(date -u +%Y-%m-%dT%H:%MZ)"
git push origin main
```

(The GitHub Action does this same step automatically every night — this is
only needed for a manual/same-day refresh.)

## 3. Redeploy the site

The site (`~/market-network/laundry`, Vercel project `laundry`,
`thelaundrymarket.com`) pulls this repo's `listings.json` from the public raw
URL at BUILD time (`prebuild` -> `scripts/fetch-listings.mjs`), so a fresh
build always republishes the latest dataset — no manual copy step needed:

```bash
cd ~/market-network/laundry
vercel --prod --yes
```

## One-liner (all three steps)

```bash
cd ~/market-network/laundry-scrapers && source .venv/bin/activate && \
python run_all.py && \
git add listings.json output/*.csv site_id_registry.json && \
git commit -m "chore(laundry): refresh listings $(date -u +%Y-%m-%dT%H:%MZ)" && \
git push origin main && \
cd ~/market-network/laundry && vercel --prod --yes
```

## Adding a new broker source

1. Create `<source>.py` in this repo (copy `lbrokers.py` as a template if the
   broker has an index page listing detail pages; copy `sunbelt.py` if it's a
   single category page with a similar "financials-in-a-card" layout).
2. Add the source to `broker_codes.json` (`sources.<key>`) with its own
   `broker_url` and `ref_prefix`. Never add a source from the blocked
   aggregator list in `broker_codes.json` / `README.md`.
3. Add `(display_name, module_name)` to the `SCRAPERS` list in `run_all.py`.
4. Add the CSV stem mapping in `normalizer.py`'s `stem_to_source` dict if the
   module name differs from the CSV filename stem.
5. Run `python run_all.py --only <module_name>` to test the new source alone,
   then a full `python run_all.py` to confirm the merge + dedupe look right.
6. Commit + push as above.

No minimum listing count applies anywhere in this pipeline — a legitimate
broker with 0 currently-active laundromats for sale should be left in as a
source (it'll contribute 0 rows honestly) rather than removed, unless it
never has any inventory at all.
