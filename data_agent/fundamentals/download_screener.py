#!/usr/bin/env python3
"""
download_screener.py
Downloads the full 10-year Excel historical data from Screener.in for the 22-stock universe.
Uses Playwright to inject the session cookie and natively click the download buttons, bypassing CSRF/Cloudflare.
"""
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Standard Nifty IT and Bank universe + Nifty 50 constituents
UNIVERSE = list(set([
    'TCS', 'INFY', 'HCLTECH', 'WIPRO', 'TECHM', 'LTIM', 'PERSISTENT', 'COFORGE', 'MPHASIS', 'LTTS',
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK', 'BANKBARODA', 'PNB', 'AUBANK', 'IDFCFIRSTB', 'FEDERALBNK', 'BANDHANBNK',
    # Remaining Nifty 50:
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'BAJAJ-AUTO', 'BAJFINANCE', 'BAJAJFINSV', 
    'BEL', 'BHARTIARTL', 'CIPLA', 'COALINDIA', 'DRREDDY', 'EICHERMOT', 'ZOMATO', 'GRASIM', 'HDFCLIFE', 
    'HINDALCO', 'HINDUNILVR', 'ITC', 'INDIGO', 'JSWSTEEL', 'JIOFIN', 'LT', 'M&M', 'MARUTI', 'MAXHEALTH', 
    'NTPC', 'NESTLEIND', 'ONGC', 'POWERGRID', 'RELIANCE', 'SBILIFE', 'SHRIRAMFIN', 'SUNPHARMA', 
    'TATACONSUM', 'TATAMOTORS', 'TATASTEEL', 'TITAN', 'TRENT', 'ULTRACEMCO'
]))

def main():
    load_dotenv()
    sessionid = os.getenv('SCREENER_SESSION_ID')
    if not sessionid:
        print("Error: SCREENER_SESSION_ID not found in .env")
        return

    output_dir = Path(__file__).parent / 'screener_data'
    output_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        
        # Inject the session cookie
        context.add_cookies([{
            'name': 'sessionid',
            'value': sessionid,
            'domain': '.screener.in',
            'path': '/'
        }])
        
        page = context.new_page()

        for symbol in UNIVERSE:
            print(f"\n[{symbol}] Processing...")
            
            # Skip if file already exists
            expected_file = output_dir / f"{symbol}.xlsx"
            if expected_file.exists():
                print(f"  -> Skipping, {symbol}.xlsx already exists.")
                continue

            try:
                # 1. Navigate to company page
                # Banks -> standalone, IT -> consolidated
                is_bank = symbol in ['HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 'INDUSINDBK', 'BANKBARODA', 'PNB', 'AUBANK', 'IDFCFIRSTB', 'FEDERALBNK', 'BANDHANBNK']
                suffix = "consolidated/" if not is_bank else ""
                screener_sym = 'LTM' if symbol == 'LTIM' else ('TMCV' if symbol == 'TATAMOTORS' else ('ETERNAL' if symbol == 'ZOMATO' else symbol))
                import random
                sleep_time = random.uniform(5, 12)
                print(f"  [~] Sleeping for {sleep_time:.1f}s to avoid rate limits...")
                time.sleep(sleep_time)
                page.goto(f"https://www.screener.in/company/{screener_sym}/{suffix}", wait_until="domcontentloaded")
                time.sleep(2) # Prevent rapid fire 429s
                # Check if logged in
                if page.locator('a[href="/login/"]').is_visible():
                    print("  [-] Session cookie appears invalid or expired.")
                    break

                # 2. Find and click Export to Excel
                export_btn = page.locator('button[aria-label="Export to Excel"]')
                if not export_btn.is_visible() and suffix:
                    print("  [~] Export button not found on consolidated page, trying standalone...")
                    page.goto(f"https://www.screener.in/company/{screener_sym}/", wait_until="domcontentloaded")
                    time.sleep(2)
                    export_btn = page.locator('button[aria-label="Export to Excel"]')

                if not export_btn.is_visible():
                    print("  [-] Could not find Export button.")
                    continue

                # 3. Wait for download
                with page.expect_download(timeout=15000) as download_info:
                    export_btn.click()
                
                download = download_info.value
                filepath = output_dir / f"{symbol}.xlsx"
                download.save_as(filepath)
                print(f"  [+] Saved {filepath.name}")

            except Exception as e:
                print(f"  [-] Failed: {e}")

        browser.close()
        print("\n✅ All downloads complete.")

if __name__ == "__main__":
    main()
