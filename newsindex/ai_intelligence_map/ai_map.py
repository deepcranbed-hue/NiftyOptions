#!/usr/bin/env python3
"""
AI Industry Intelligence Map  —  self-run, zero-cost tracker for the whole AI
value chain (semis → models → cloud → networking → power → applications).

What it does each run
---------------------
1. Reads the curated intelligence in `registry.py` (moats, risks, dependencies,
   KPIs, phase, bull/bear, source URLs).
2. Pulls LIVE quantitative data for every public ticker via yfinance
   (price, market cap, P/E, revenue growth, gross & net margin, 52-week move,
   analyst target).  Blocked network / offline → those fields show "—" and the
   curated map still renders.
3. Consolidates fresh NEWS from AI-ecosystem RSS feeds and tags each headline
   to the companies + layers it mentions.
4. Writes three outputs to ./reports/ (dated):
      • ai_map_<date>.html  — interactive dashboard (sort / filter / search)
      • ai_map_<date>.csv   — one row per company (curated + live)
      • ai_map_<date>.md    — Markdown snapshot you can diff over time

Usage
-----
    pip install -r requirements.txt
    python3 ai_map.py                 # full run (live data + news)
    python3 ai_map.py --no-live       # skip yfinance (curated + news only)
    python3 ai_map.py --no-news       # skip RSS news
    python3 ai_map.py --fetch-sources # ALSO fetch a snippet from each curated
                                      #   source URL (uses trafilatura; optional
                                      #   Playwright fallback for blocked pages)

Everything runs on your machine. No paid APIs.
"""

import argparse
import csv
import datetime as dt
import html
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import registry as R

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# --- AI-ecosystem news feeds (edit freely) -----------------------------------
NEWS_FEEDS = [
    ("Tom's Hardware",   "https://www.tomshardware.com/feeds/all"),
    ("The Verge",        "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica",     "https://feeds.arstechnica.com/arstechnica/index"),
    ("CNBC Technology",  "https://search.cnbc.com/rss/2.0/id/19854910/device/rss/rss.html"),
    ("SemiAnalysis",     "https://www.semianalysis.com/feed"),
    ("VentureBeat AI",   "https://venturebeat.com/category/ai/feed/"),
    ("Reuters Tech",     "https://www.reutersagency.com/feed/?best-topics=tech&post_type=best"),
]

# Extra keywords per company for news tagging (name + ticker are auto-added)
EXTRA_KEYWORDS = {
    "NVIDIA": ["nvidia", "blackwell", "rubin", "cuda", "jensen"],
    "AMD": ["amd", "instinct", "mi300", "mi350", "rocm", "epyc"],
    "Broadcom": ["broadcom", "avgo", "xpu", "tomahawk"],
    "Marvell": ["marvell"],
    "TSMC": ["tsmc", "taiwan semi", "cowos"],
    "Micron": ["micron", "hbm"],
    "SK Hynix": ["sk hynix", "hynix", "hbm"],
    "Qualcomm": ["qualcomm", "snapdragon"],
    "Intel": ["intel", "gaudi", "foundry"],
    "Arm Holdings": ["arm holdings", "arm ", "arm cpu"],
    "ASML": ["asml", "euv", "lithography"],
    "OpenAI": ["openai", "chatgpt", "gpt-", "sora", "sam altman"],
    "Anthropic": ["anthropic", "claude"],
    "Google DeepMind (Alphabet)": ["google", "alphabet", "gemini", "deepmind", "tpu"],
    "Meta AI (Llama)": ["meta ", "llama", "zuckerberg", "mtia"],
    "xAI": ["xai", "grok", "colossus"],
    "Mistral AI": ["mistral"],
    "Cohere": ["cohere"],
    "Microsoft": ["microsoft", "azure", "copilot", "maia"],
    "Amazon": ["amazon", "aws", "trainium", "inferentia", "bedrock"],
    "Oracle": ["oracle", "oci"],
    "CoreWeave": ["coreweave"],
    "Arista Networks": ["arista"],
    "Vertiv": ["vertiv", "liquid cooling"],
    "Eaton": ["eaton"],
    "Schneider Electric": ["schneider"],
    "GE Vernova": ["ge vernova", "vernova", "gas turbine"],
    "Apple": ["apple", "apple intelligence", "siri"],
    "Adobe": ["adobe", "firefly"],
    "Salesforce": ["salesforce", "agentforce"],
    "ServiceNow": ["servicenow", "now assist"],
    "Intuit": ["intuit", "turbotax", "quickbooks"],
    "SAP": ["sap ", "joule"],
    "Autodesk": ["autodesk"],
}


# =============================================================================
# LIVE MARKET DATA (yfinance)
# =============================================================================
def _humanize_cap(v):
    if not v:
        return "—"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"${v/div:.2f}{unit}"
    return f"${v:,.0f}"


def _pct(v):
    if v is None:
        return "—"
    return f"{v*100:+.1f}%"


def fetch_one(ticker):
    """Return a dict of live metrics for one ticker (or Nones on failure)."""
    import yfinance as yf
    out = {"price": None, "currency": "", "market_cap": None, "pe": None,
           "fwd_pe": None, "rev_growth": None, "gross_margin": None,
           "profit_margin": None, "chg_52w": None, "target": None, "name": None}
    try:
        info = yf.Ticker(ticker).info or {}
        out["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        out["currency"] = info.get("currency", "")
        out["market_cap"] = info.get("marketCap")
        out["pe"] = info.get("trailingPE")
        out["fwd_pe"] = info.get("forwardPE")
        out["rev_growth"] = info.get("revenueGrowth")
        out["gross_margin"] = info.get("grossMargins")
        out["profit_margin"] = info.get("profitMargins")
        out["chg_52w"] = info.get("52WeekChange") or info.get("fiftyTwoWeekChange")
        out["target"] = info.get("targetMeanPrice")
        out["name"] = info.get("shortName")
    except Exception as e:
        out["_error"] = str(e)[:120]
    return out


def fetch_live(tickers, workers=8):
    """Fetch live metrics for many tickers concurrently, with graceful fail."""
    results = {}
    try:
        import yfinance  # noqa: F401
    except Exception:
        print("  [live] yfinance not installed — skipping live data.")
        return {t: {} for t in tickers}

    print(f"  [live] fetching {len(tickers)} tickers via yfinance ...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, t): t for t in tickers}
        got = 0
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                results[t] = fut.result()
                if results[t].get("price"):
                    got += 1
            except Exception as e:
                results[t] = {"_error": str(e)[:120]}
    print(f"  [live] got prices for {got}/{len(tickers)} tickers "
          f"({'network OK' if got else 'network blocked / offline — curated data still renders'}).")
    return results


# =============================================================================
# NEWS CONSOLIDATION (RSS)
# =============================================================================
def _keywords_for(company):
    kws = set(EXTRA_KEYWORDS.get(company["name"], []))
    kws.add(company["name"].lower())
    if company["ticker"]:
        kws.add(company["ticker"].split(".")[0].lower())
    return {k for k in kws if len(k) >= 3}


def fetch_news(max_per_feed=40):
    """Pull RSS headlines and tag each to companies + layers."""
    try:
        import feedparser
    except Exception:
        print("  [news] feedparser not installed — skipping news.")
        return [], {}

    print(f"  [news] reading {len(NEWS_FEEDS)} feeds ...")
    kw_index = {c["name"]: _keywords_for(c) for c in R.COMPANIES}
    items = []
    seen = set()
    for source, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for e in feed.entries[:max_per_feed]:
            title = getattr(e, "title", "").strip()
            link = getattr(e, "link", "")
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            summary = getattr(e, "summary", "")
            blob = f"{title} {summary}".lower()
            tagged = [name for name, kws in kw_index.items()
                      if any(k in blob for k in kws)]
            if tagged:  # only keep headlines relevant to a tracked company
                items.append({"source": source, "title": title, "link": link,
                              "companies": tagged})
    # per-company index
    by_company = {}
    for it in items:
        for name in it["companies"]:
            by_company.setdefault(name, []).append(it)
    print(f"  [news] {len(items)} relevant headlines "
          f"({'network OK' if items else 'no items — network blocked / offline'}).")
    return items, by_company


# =============================================================================
# OPTIONAL: fetch a snippet from each curated source URL
# =============================================================================
def fetch_source_snippet(url, timeout=15):
    """Best-effort readable snippet from a URL. trafilatura first, then an
    optional Playwright fallback for bot-blocked / JS pages (like market_scan)."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False) or ""
            if text.strip():
                return text.strip()[:400]
    except Exception:
        pass
    # Optional Playwright fallback (only if installed; heavy).
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(url, timeout=timeout * 1000)
            txt = pg.inner_text("body")[:400]
            b.close()
            return txt.strip()
    except Exception:
        return ""


def fetch_all_sources():
    urls = {}
    for c in R.COMPANIES:
        for u in c.get("sources", []):
            urls.setdefault(u, [])
        urls.setdefault(list(c.get("sources", ["—"]))[0] if c.get("sources") else "—", [])
    for label, u in R.SECTOR_SOURCES.items():
        urls.setdefault(u, [])
    snippets = {}
    print(f"  [sources] fetching {len(urls)} source URLs (best-effort) ...")
    for u in list(urls):
        if u and u.startswith("http"):
            snippets[u] = fetch_source_snippet(u)
            time.sleep(0.3)
    got = sum(1 for v in snippets.values() if v)
    print(f"  [sources] got readable text from {got}/{len(snippets)} URLs.")
    return snippets


# =============================================================================
# ASSEMBLE ROWS
# =============================================================================
def build_rows(live, by_company):
    rows = []
    for c in R.COMPANIES:
        m = live.get(c["ticker"], {}) if c["ticker"] else {}
        news = by_company.get(c["name"], [])
        rows.append({
            **c,
            "layer_label": R.LAYERS[c["layer"]],
            "phase_label": R.PHASES[c["phase"]],
            "price": m.get("price"),
            "currency": m.get("currency", ""),
            "market_cap": m.get("market_cap"),
            "market_cap_h": _humanize_cap(m.get("market_cap")),
            "pe": m.get("pe"),
            "fwd_pe": m.get("fwd_pe"),
            "rev_growth": m.get("rev_growth"),
            "gross_margin": m.get("gross_margin"),
            "profit_margin": m.get("profit_margin"),
            "chg_52w": m.get("chg_52w"),
            "target": m.get("target"),
            "news": news,
        })
    return rows


# =============================================================================
# OUTPUT: CSV
# =============================================================================
def write_csv(rows, path):
    cols = ["layer_label", "name", "ticker", "sublayer", "phase_label",
            "price", "currency", "market_cap", "pe", "fwd_pe", "rev_growth",
            "gross_margin", "profit_margin", "chg_52w", "target",
            "role", "moat", "risk", "depends_on", "customers",
            "kpis", "bull", "bear", "priv_val", "news_count", "sources"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([
                r["layer_label"], r["name"], r["ticker"] or "", r["sublayer"],
                r["phase_label"], r["price"] or "", r["currency"],
                r["market_cap"] or "", r["pe"] or "", r["fwd_pe"] or "",
                r["rev_growth"] or "", r["gross_margin"] or "",
                r["profit_margin"] or "", r["chg_52w"] or "", r["target"] or "",
                r["role"], r["moat"], r["risk"], r["depends_on"], r["customers"],
                " | ".join(r["kpis"]), r["bull"], r["bear"], r["priv_val"] or "",
                len(r["news"]), " ; ".join(r.get("sources", [])),
            ])


# =============================================================================
# OUTPUT: Markdown snapshot
# =============================================================================
def write_markdown(rows, news_items, path, stamp):
    lines = [f"# AI Industry Intelligence Map — {stamp}", ""]
    lines.append("_Curated value-chain map with live market metrics + tagged news. "
                 "Generated by ai_map.py._\n")
    # summary table
    lines.append("## Snapshot by layer\n")
    for lk, label in R.LAYERS.items():
        lrows = [r for r in rows if r["layer"] == lk]
        if not lrows:
            continue
        lines.append(f"### {label}\n")
        lines.append("| Company | Ticker | Mkt Cap | P/E | Rev g | 52w | Moat | Biggest risk |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in lrows:
            lines.append(
                f"| {r['name']} | {r['ticker'] or '—'} | {r['market_cap_h']} | "
                f"{_fmt_num(r['pe'])} | {_pct(r['rev_growth'])} | {_pct(r['chg_52w'])} | "
                f"{r['moat']} | {r['risk']} |")
        lines.append("")
    # news
    if news_items:
        lines.append("## Tagged news (this run)\n")
        for it in news_items[:60]:
            comps = ", ".join(it["companies"])
            lines.append(f"- **[{comps}]** {it['title']} — _{it['source']}_ ({it['link']})")
        lines.append("")
    # sources
    lines.append("## Source library\n")
    for label, u in R.SECTOR_SOURCES.items():
        lines.append(f"- {label}: {u}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _fmt_num(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}"
    except Exception:
        return "—"


# =============================================================================
# OUTPUT: interactive HTML dashboard
# =============================================================================
LAYER_COLORS = {
    "L1_SEMI": "#7c9cff", "L2_MODELS": "#68d391", "L3_CLOUD": "#f6ad55",
    "L4_NET": "#4fd1c5", "L5_POWER": "#f687b3", "L6_APPS": "#b794f4",
}


def write_html(rows, news_items, path, stamp, live_ok):
    def esc(x):
        return html.escape(str(x)) if x is not None else ""

    layers_json = list(R.LAYERS.items())

    # Build per-row cards grouped by layer
    body_sections = []
    for lk, label in layers_json:
        lrows = [r for r in rows if r["layer"] == lk]
        if not lrows:
            continue
        color = LAYER_COLORS.get(lk, "#7c9cff")
        cards = []
        for r in lrows:
            news_html = ""
            if r["news"]:
                items = "".join(
                    f'<li><a href="{esc(n["link"])}" target="_blank" rel="noopener">'
                    f'{esc(n["title"])}</a> <span class="src">{esc(n["source"])}</span></li>'
                    for n in r["news"][:5])
                news_html = f'<div class="news"><b>News ({len(r["news"])})</b><ul>{items}</ul></div>'
            src_html = ""
            if r.get("sources"):
                links = " · ".join(
                    f'<a href="{esc(u)}" target="_blank" rel="noopener">{_domain(u)}</a>'
                    for u in r["sources"])
                src_html = f'<div class="sources">Sources: {links}</div>'
            kpis = "".join(f"<span class='kpi'>{esc(k)}</span>" for k in r["kpis"])
            valuation = (r["market_cap_h"] if r["ticker"]
                         else (esc(r["priv_val"]) or "private"))
            metrics = ""
            if r["ticker"]:
                metrics = (
                    f'<div class="metrics">'
                    f'<span title="Market cap">💰 {r["market_cap_h"]}</span>'
                    f'<span title="Price">{("$"+format(r["price"],",.2f")) if r["price"] else "—"}</span>'
                    f'<span title="Trailing P/E">P/E {_fmt_num(r["pe"])}</span>'
                    f'<span title="Revenue growth">Rev {_pct(r["rev_growth"])}</span>'
                    f'<span title="Gross margin">GM {_pct(r["gross_margin"])}</span>'
                    f'<span title="52-week change">52w {_pct(r["chg_52w"])}</span>'
                    f'</div>')
            else:
                metrics = f'<div class="metrics"><span class="priv">🔒 Private — {valuation}</span></div>'

            search_blob = esc(" ".join([
                r["name"], r["ticker"] or "", r["sublayer"], r["role"],
                r["moat"], r["risk"], r["depends_on"], r["customers"],
                r["phase_label"]]).lower())

            cards.append(f"""
      <div class="card" data-layer="{lk}" data-phase="{r['phase']}"
           data-ticker="{esc(r['ticker'] or '')}" data-mcap="{r['market_cap'] or 0}"
           data-search="{search_blob}">
        <div class="card-head">
          <div class="title">{esc(r['name'])}
            <span class="tkr">{esc(r['ticker'] or 'private')}</span></div>
          <span class="phase p{r['phase']}">P{r['phase']}</span>
        </div>
        <div class="sub">{esc(r['sublayer'])}</div>
        {metrics}
        <div class="role">{esc(r['role'])}</div>
        <div class="grid2">
          <div class="fld moat"><b>Moat</b>{esc(r['moat'])}</div>
          <div class="fld risk"><b>Risk</b>{esc(r['risk'])}</div>
          <div class="fld"><b>Depends on</b>{esc(r['depends_on'])}</div>
          <div class="fld"><b>Customers</b>{esc(r['customers'])}</div>
        </div>
        <div class="kpis"><b>KPIs</b> {kpis}</div>
        <div class="thesis">
          <div class="bull"><b>Bull</b> {esc(r['bull'])}</div>
          <div class="bear"><b>Bear</b> {esc(r['bear'])}</div>
        </div>
        {news_html}
        {src_html}
      </div>""")
        body_sections.append(f"""
    <section class="layer" data-layer="{lk}">
      <h2 style="border-color:{color}"><span class="dot" style="background:{color}"></span>{esc(label)}
        <span class="count">{len(lrows)}</span></h2>
      <div class="cards">{''.join(cards)}</div>
    </section>""")

    # news ticker
    news_html_top = ""
    if news_items:
        lis = "".join(
            f'<li><a href="{esc(n["link"])}" target="_blank" rel="noopener">{esc(n["title"])}</a> '
            f'<span class="src">— {esc(n["source"])} · {esc(", ".join(n["companies"]))}</span></li>'
            for n in news_items[:40])
        news_html_top = f'<details class="newsfeed"><summary>📰 Tagged news this run ({len(news_items)})</summary><ul>{lis}</ul></details>'

    live_badge = ('<span class="badge ok">live market data ✓</span>' if live_ok
                  else '<span class="badge off">offline — curated data only (run on a networked machine for live numbers)</span>')

    phase_legend = " ".join(
        f'<span class="phase p{k}">P{k}</span> {esc(v)}' for k, v in R.PHASES.items())

    doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Industry Intelligence Map — {esc(stamp)}</title>
<style>
:root {{ --bg:#0b0f1a; --panel:#141a2b; --panel2:#1b2338; --ink:#e8ecf6;
  --muted:#95a0bd; --line:#25304d; --good:#68d391; --bad:#fc8181; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
header {{ position:sticky; top:0; z-index:10; background:rgba(11,15,26,.92);
  backdrop-filter:blur(8px); border-bottom:1px solid var(--line); padding:14px 20px; }}
h1 {{ margin:0 0 4px; font-size:20px; letter-spacing:.2px; }}
.meta {{ color:var(--muted); font-size:12px; }}
.badge {{ font-size:11px; padding:2px 8px; border-radius:20px; margin-left:8px; }}
.badge.ok {{ background:#123524; color:var(--good); }}
.badge.off {{ background:#3a2020; color:var(--bad); }}
.controls {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; align-items:center; }}
.controls input, .controls select {{ background:var(--panel2); color:var(--ink);
  border:1px solid var(--line); border-radius:8px; padding:7px 10px; font-size:13px; }}
.controls input#q {{ min-width:240px; flex:1; }}
.pill {{ cursor:pointer; user-select:none; background:var(--panel2); border:1px solid var(--line);
  color:var(--muted); padding:6px 11px; border-radius:20px; font-size:12px; }}
.pill.active {{ color:#fff; border-color:#5b7cff; background:#22305c; }}
main {{ padding:18px 20px 60px; max-width:1500px; margin:0 auto; }}
.legend {{ color:var(--muted); font-size:12px; margin:6px 0 14px; }}
.newsfeed {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:8px 14px; margin:0 0 16px; }}
.newsfeed summary {{ cursor:pointer; font-weight:600; }}
.newsfeed ul {{ margin:10px 0 4px; padding-left:18px; columns:2; }}
.newsfeed li {{ margin:0 0 7px; break-inside:avoid; }}
.newsfeed a {{ color:#cdd8ff; }}
.src {{ color:var(--muted); font-size:11px; }}
section.layer {{ margin:0 0 26px; }}
section.layer h2 {{ font-size:16px; margin:0 0 12px; padding:6px 0 6px 2px;
  border-bottom:2px solid; display:flex; align-items:center; gap:9px; }}
.dot {{ width:11px; height:11px; border-radius:50%; display:inline-block; }}
.count {{ font-size:11px; color:var(--muted); background:var(--panel2);
  border-radius:20px; padding:1px 9px; margin-left:4px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:14px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
  padding:14px; display:flex; flex-direction:column; gap:8px; }}
.card-head {{ display:flex; justify-content:space-between; align-items:center; }}
.title {{ font-weight:700; font-size:15px; }}
.tkr {{ color:var(--muted); font-weight:500; font-size:11px; margin-left:6px; }}
.sub {{ color:var(--muted); font-size:12px; margin-top:-4px; }}
.phase {{ font-size:10px; font-weight:700; padding:2px 7px; border-radius:20px; color:#0b0f1a; }}
.p1 {{ background:#7c9cff; }} .p2 {{ background:#f6ad55; }}
.p3 {{ background:#68d391; }} .p4 {{ background:#b794f4; }}
.metrics {{ display:flex; flex-wrap:wrap; gap:6px 12px; font-size:12px;
  color:#cdd8ff; background:var(--panel2); border-radius:8px; padding:7px 10px; }}
.metrics .priv {{ color:#f6c177; }}
.role {{ font-size:13px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
.fld {{ font-size:12px; background:var(--panel2); border-radius:8px; padding:7px 9px; }}
.fld b {{ display:block; color:var(--muted); font-size:10px; text-transform:uppercase;
  letter-spacing:.5px; margin-bottom:2px; }}
.fld.moat {{ box-shadow:inset 3px 0 0 var(--good); }}
.fld.risk {{ box-shadow:inset 3px 0 0 var(--bad); }}
.kpis {{ font-size:11px; color:var(--muted); }}
.kpi {{ display:inline-block; background:#22305c; color:#cdd8ff; border-radius:6px;
  padding:1px 7px; margin:2px 3px 0 0; font-size:11px; }}
.thesis {{ font-size:12px; display:flex; flex-direction:column; gap:4px; }}
.bull b {{ color:var(--good); }} .bear b {{ color:var(--bad); }}
.news {{ font-size:12px; border-top:1px dashed var(--line); padding-top:7px; }}
.news ul {{ margin:5px 0 0; padding-left:16px; }} .news li {{ margin:0 0 4px; }}
.news a {{ color:#cdd8ff; }}
.sources {{ font-size:11px; color:var(--muted); border-top:1px dashed var(--line); padding-top:6px; }}
.sources a {{ color:#8fa6ff; }}
.hidden {{ display:none !important; }}
footer {{ color:var(--muted); font-size:11px; text-align:center; padding:20px; }}
</style></head>
<body>
<header>
  <h1>🧠 AI Industry Intelligence Map {live_badge}</h1>
  <div class="meta">Generated {esc(stamp)} · {len(rows)} companies · 6 layers · live metrics via yfinance, news via RSS. Edit <code>registry.py</code> to extend.</div>
  <div class="controls">
    <input id="q" placeholder="🔎 search company, moat, risk, dependency…">
    <select id="sort">
      <option value="layer">Sort: by layer</option>
      <option value="mcap">Sort: market cap ↓</option>
      <option value="name">Sort: name A–Z</option>
      <option value="phase">Sort: phase</option>
    </select>
    <span class="pill layer-pill active" data-f="all">All layers</span>
    {''.join(f'<span class="pill layer-pill" data-f="{k}">{esc(v.split(chr(8212))[0].strip())}</span>' for k,v in layers_json)}
    <span style="width:12px"></span>
    {''.join(f'<span class="pill phase-pill" data-p="{k}">P{k}</span>' for k in R.PHASES)}
  </div>
  <div class="legend">Phases: {phase_legend}</div>
</header>
<main>
  {news_html_top}
  <div id="board">{''.join(body_sections)}</div>
  <footer>Self-run · zero-cost · curated in registry.py · live numbers from Yahoo Finance, headlines from public RSS.<br>
  Not investment advice — a research map. Verify private-company valuations against the linked sources.</footer>
</main>
<script>
const q=document.getElementById('q'), sortSel=document.getElementById('sort');
let layerFilter='all', phaseFilter=new Set();
function apply(){{
  const term=q.value.trim().toLowerCase();
  document.querySelectorAll('.card').forEach(c=>{{
    const okL = layerFilter==='all' || c.dataset.layer===layerFilter;
    const okP = phaseFilter.size===0 || phaseFilter.has(c.dataset.phase);
    const okS = !term || c.dataset.search.includes(term);
    c.classList.toggle('hidden', !(okL&&okP&&okS));
  }});
  document.querySelectorAll('section.layer').forEach(s=>{{
    const any=[...s.querySelectorAll('.card')].some(c=>!c.classList.contains('hidden'));
    s.classList.toggle('hidden', !any);
  }});
}}
q.addEventListener('input', apply);
document.querySelectorAll('.layer-pill').forEach(p=>p.addEventListener('click',()=>{{
  document.querySelectorAll('.layer-pill').forEach(x=>x.classList.remove('active'));
  p.classList.add('active'); layerFilter=p.dataset.f; apply();
}}));
document.querySelectorAll('.phase-pill').forEach(p=>p.addEventListener('click',()=>{{
  p.classList.toggle('active');
  const k=p.dataset.p; phaseFilter.has(k)?phaseFilter.delete(k):phaseFilter.add(k); apply();
}}));
sortSel.addEventListener('change',()=>{{
  const mode=sortSel.value;
  document.querySelectorAll('section.layer .cards').forEach(box=>{{
    const cards=[...box.children];
    cards.sort((a,b)=>{{
      if(mode==='mcap') return (+b.dataset.mcap)-(+a.dataset.mcap);
      if(mode==='name') return a.querySelector('.title').textContent.localeCompare(b.querySelector('.title').textContent);
      if(mode==='phase') return (+a.dataset.phase)-(+b.dataset.phase);
      return 0;
    }});
    cards.forEach(c=>box.appendChild(c));
  }});
}});
</script>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def _domain(url):
    try:
        return url.split("//", 1)[1].split("/", 1)[0].replace("www.", "")
    except Exception:
        return url


# =============================================================================
# MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="AI Industry Intelligence Map")
    ap.add_argument("--no-live", action="store_true", help="skip yfinance live data")
    ap.add_argument("--no-news", action="store_true", help="skip RSS news")
    ap.add_argument("--fetch-sources", action="store_true",
                    help="also fetch a snippet from each curated source URL")
    args = ap.parse_args()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    now = dt.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    fstamp = now.strftime("%Y-%m-%d_%H%M")

    print(f"AI Industry Intelligence Map — {stamp}")
    print(f"  {len(R.COMPANIES)} companies across {len(R.LAYERS)} layers.")

    live = {}
    if not args.no_live:
        live = fetch_live(R.all_tickers())
    live_ok = any(v.get("price") for v in live.values())

    news_items, by_company = ([], {})
    if not args.no_news:
        news_items, by_company = fetch_news()

    if args.fetch_sources:
        fetch_all_sources()  # printed best-effort; snippets not embedded by default

    rows = build_rows(live, by_company)

    html_path = os.path.join(REPORTS_DIR, f"ai_map_{fstamp}.html")
    csv_path = os.path.join(REPORTS_DIR, f"ai_map_{fstamp}.csv")
    md_path = os.path.join(REPORTS_DIR, f"ai_map_{fstamp}.md")

    write_html(rows, news_items, html_path, stamp, live_ok)
    write_csv(rows, csv_path)
    write_markdown(rows, news_items, md_path, stamp)

    print("\nWrote:")
    print(f"  {html_path}")
    print(f"  {csv_path}")
    print(f"  {md_path}")
    print("\nOpen the .html for the interactive map. Edit registry.py to add/adjust companies.")


if __name__ == "__main__":
    main()
