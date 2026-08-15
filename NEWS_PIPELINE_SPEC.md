# News Pipeline — Filings Delta Spec

**Status:** Design (v0.2, 2026-07-10). Corrected: the RSS pipeline already exists.
**Owner:** deep

## What already exists (do not rebuild)

The news pipeline is live in `backend/`. Current flow (`get_tagged_news` in `backend/main.py`):

```
fetch_rss()                         # backend/quant/rss_news.py — ET, Moneycontrol,
                                    #   Livemint, Business Standard, + global feeds
  -> prepare_articles(max_age=12h)  # backend/quant/news_window.py — recency window + dedupe
  -> is_relevant()  filter          # backend/quant/news_provenance.py — drop off-universe noise
  -> scan_batch()   quarantine      # pre-LLM prompt-injection guard (quarantine + reason)
  -> llm_tag_batch()                # Gemini tagging: sentiment, sectors_affected,
                                    #   constituents, event_code, confidence, cluster_id
                                    #   (keyword fallback if the LLM is unavailable)
  -> sector_sentiment_from_gemini() # source_tier() trust weighting + weighted median,
                                    #   cluster_signature() cross-feed dedup, __audit trail
  -> sector_news_cache.json         # cached output
  -> market_view compare/suggest    # feeds the desk
```

Each stage already handles dedupe, recency weighting, relevance, injection safety, and LLM tagging. The tagger emits exactly the structured fields a signal needs (`sentiment`, `sectors_affected`, `constituents`, `event_code`, `confidence`). Aggregation is provenance-aware: `news_provenance.py` supplies **source trust tiers** (exchange filing 1.0 → live-blog 0.20, which breaks the circular price→sentiment loop), a **pre-LLM ingest scanner** that quarantines injection/junk *with a reason* (never a silent drop), and **cross-feed cluster dedup**; `sector_tagging.py` then aggregates with a **weighted median** and records a run-level `__audit` block. **This is the pipeline; we extend it, we don't replace it.**

## The only gap: exchange corporate filings

RSS gives press coverage. It does **not** give primary-source corporate filings (board meetings, results, M&A, rating actions) at the moment they hit the exchange. That's the delta. The fix is one new fetcher that emits the **same article-dict shape** `fetch_rss()` returns, so it merges in with zero downstream changes.

### Access strategy (the "site won't let me download" fix)
- **BSE** publishes a machine-readable announcements JSON (`api.bseindia.com/BseIndiaAPI/api/AnnGetData/w`) — headline, category, scrip code, PDF link, timestamp. This is the practical route and covers most NSE-listed names (dual-listed).
- **NSE** filing PDFs are static, downloadable files under `https://nsearchives.nseindia.com/corporate/...`. Reach them via the announcement item that references them. **Do not** touch `www.nseindia.com/api/*` — it needs live browser cookies and its ToS forbids automated use (that's the wall you hit).
- Everything stays polite: User-Agent, timeout, cache between polls — same discipline as `rss_news.py`.

## New module: `backend/quant/filings.py`

```python
def fetch_filings(scrip_codes: dict | None = None, per_source: int = 40) -> list[dict]:
    """Poll BSE announcements (+ NSE archive references) and return article dicts
    in the SAME shape as fetch_rss():
        {"title", "publishedAt"(ISO UTC), "description", "source"}
    plus two optional passthrough keys the tagger can use if present:
        "event_code"  -> e.g. 'board_meeting'|'results'|'mna'|'rating'  (from BSE category)
        "symbol"      -> resolved constituent symbol (from scrip_code map)
    Network failure on one source never kills the batch (mirror fetch_rss)."""
```

- **Title** = filing headline. **Description** = category + short subject (and, for high-value categories like results/board-meeting/M&A, optionally the first N chars of the PDF text via the existing PDF tooling — otherwise skip the PDF fetch to stay light).
- **`event_code`** is pre-filled from the BSE announcement category, giving the Gemini tagger a strong prior (filings are cleaner-labelled than press).
- **`symbol`** resolved from BSE `scrip_code` → constituent symbol using `strategy_framework/config/constituents.py` / `nifty-50-stock-list.csv`.
- Publish timestamps stored ISO-8601 **UTC** (matches `rss_news._to_iso_utc`).

## Wiring it in (one line)

In `get_tagged_news` (`backend/main.py`), change:

```python
raw = fetch_rss()
```
to:
```python
raw = fetch_rss() + fetch_filings()
```

That's the whole integration. `prepare_articles` dedupes across both, `is_relevant` keeps filings (they're on-universe by construction), `scan_batch` guards them, and `llm_tag_batch` tags them into the same `sector_news_cache.json`. Filings inherit every existing safety and relevance guarantee for free.

Consider a slightly longer recency window for filings than the 12h RSS window (filings matter for a day or two), or tag them with a longer decay in the tagger.

## Verify at build time
- BSE JSON endpoint path/params (they version it) and the exact category codes.
- `scrip_code` → symbol map covers all 50 constituents.
- One live `fetch_filings()` call, eyeball the dicts against a `fetch_rss()` dict — shapes must be identical.

## Explicitly NOT doing now (deferred)
The v0.1 draft proposed a parallel SQLite+FTS5+vector store, local Ollama extractor, and a standalone impact scorer. All redundant with the working Gemini pipeline and the JSON cache. Revisit only if you later want: (a) a searchable news history (then add an FTS5 archive table that `get_tagged_news` also writes to), or (b) to move tagging off Gemini to a local model (swap inside `llm_tag_batch`, everything else stays).

## Optional: a `news_flow` signal
If you want news to feed the trade blend (not just the desk panel), wrap the cached tags in a `strategy_framework` `Signal` (`score∈[-1,1]` from index-weighted sentiment, `confidence` from tag confidence), shipped at **weight 0.0** / `tag='PRIOR'` until ≥60 sessions validate it — per the framework HARD RULES. This is independent of the filings work.
