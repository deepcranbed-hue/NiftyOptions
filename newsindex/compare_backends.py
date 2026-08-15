#!/usr/bin/env python3
"""
compare_backends.py
-------------------
One file to run. Compares the three article extractors — trafilatura,
crawl4ai, and Playwright — on the same URL(s), so you can see the difference
in coverage, numbers pulled, and speed.

Usage:
    python3 compare_backends.py                 # uses the sample URLs below
    python3 compare_backends.py "<article_url>" # compare one specific URL

Install (as needed):
    pip3 install trafilatura lxml_html_clean
    pip3 install crawl4ai && crawl4ai-setup
    pip3 install playwright && playwright install chromium
"""

import sys
import time

import fetch_article as F

# Default test set: one usually-open page + one bot-blocked ET page.
SAMPLE_URLS = [
    "https://www.moneycontrol.com/news/business/ltimindtree-q4-preview-lower-pass-through-revenue-seen-impacting-growth-marginally_17531881.html",
    "https://economictimes.indiatimes.com/markets/stocks/news/hcl-techs-rs-3500-crore-ai-data-centre-foray-a-new-growth-engine-or-capital-intensive-diversion/articleshow/132383949.cms",
]

BACKENDS = [
    ("trafilatura", F._trafilatura_fetch),
    ("crawl4ai",    F._crawl4ai_fetch),
    ("playwright",  F._playwright_fetch),
]


def run_one(url: str):
    print("\n" + "=" * 78)
    print("URL:", url)
    print("=" * 78)
    print(f"{'backend':13}{'secs':>7}{'chars':>8}{'#nums':>7}  lead / status")
    print("-" * 78)
    for name, fn in BACKENDS:
        t0 = time.time()
        try:
            title, body = fn(url)
        except Exception as e:
            title, body = "", f"[error: {str(e)[:40]}]"
        secs = time.time() - t0
        nums = len(F.extract_numbers(body)) if body and not body.startswith("[error") else 0
        lead = F.first_paragraph(body)[:70].replace("\n", " ") if body else "(nothing / not installed)"
        print(f"{name:13}{secs:>7.1f}{len(body):>8}{nums:>7}  {lead}")
    print("-" * 78)
    print("Higher chars + more numbers = better extraction. Higher secs = heavier/hotter.")


def main():
    urls = sys.argv[1:] or SAMPLE_URLS
    for u in urls:
        run_one(u)
    print("\nTakeaways to look for:")
    print(" • On open pages (Moneycontrol/Livemint) trafilatura usually ties crawl4ai — keep it (cool).")
    print(" • On bot-blocked pages (ET/Indiatimes) crawl4ai & playwright beat trafilatura.")
    print(" • If crawl4ai wins on the blocked page, set USE_CRAWL4AI=True, ALLOW_PLAYWRIGHT=False")
    print("   in fetch_article.py — one browser backend instead of two.")


if __name__ == "__main__":
    main()
