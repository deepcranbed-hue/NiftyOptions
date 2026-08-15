import urllib.request
import urllib.parse
import json
import os
import sys
from datetime import datetime, timedelta

BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"

def fetch_bse_announcements(scrip_code: int = 532540, days_back: int = 5):
    """
    Fetches announcements from BSE for a given company using correct parameters.
    TCS scrip code is 532540.
    """
    today = datetime.now()
    prev_date = (today - timedelta(days=days_back)).strftime("%Y%m%d")
    to_date = today.strftime("%Y%m%d")

    params = {
        "strCat": "-1",
        "strPrevDate": prev_date,
        "strScrip": str(scrip_code),
        "strSearch": "P",
        "strToDate": to_date,
        "strType": "C",
        "pageno": "1"
    }
    
    query_string = urllib.parse.urlencode(params)
    url = f"{BSE_ANN_URL}?{query_string}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bseindia.com/corporates/ann.html",
        "Accept": "application/json, text/plain, */*",
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        print(f"Fetching BSE announcements from: {url}")
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f"Error fetching BSE announcements: {e}")
        return None

def main():
    scrip_code = 532540 # TCS
    if len(sys.argv) > 1:
        try:
            scrip_code = int(sys.argv[1])
        except ValueError:
            print("Error: Scrip code must be an integer.")
            sys.exit(1)

    # Let's search announcements in the last 10 days
    data = fetch_bse_announcements(scrip_code, days_back=10)
    if not data:
        print("No announcement data returned or invalid format.")
        sys.exit(1)

    # BSE returns a dict with "Table" key containing list of rows
    rows = data.get("Table") if isinstance(data, dict) else data
    if not rows or not isinstance(rows, list):
        print("No announcement list found in response. Payload format:", type(data))
        sys.exit(1)

    print(f"\nFetched {len(rows)} announcements. Latest announcements:")
    
    # Filter for results or earnings-related filings
    results_filings = []
    for item in rows:
        subj = (item.get("NEWSSUB") or "").upper()
        head = (item.get("HEADLINE") or "").upper()
        cat = (item.get("CATEGORYNAME") or "").upper()
        
        is_results = "RESULT" in subj or "RESULT" in head or "RESULT" in cat or "FINANCIAL" in subj or "FINANCIAL" in head
        
        # Format the item
        filing = {
            "headline": item.get("HEADLINE"),
            "category": item.get("CATEGORYNAME"),
            "published_at": item.get("NEWS_DT"),
            "attachment": item.get("ATTACHMENTNAME"),
            "is_results": is_results
        }
        if is_results:
            results_filings.append(filing)

    print("\n--- Latest Corporate Announcements ---")
    for i, item in enumerate(rows[:10]):
        attach = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{item.get('ATTACHMENTNAME')}" if item.get('ATTACHMENTNAME') else "No Attachment"
        print(f"{i+1}. Date: {item.get('NEWS_DT')}")
        print(f"   Headline: {item.get('HEADLINE')}")
        print(f"   Category: {item.get('CATEGORYNAME')}")
        print(f"   Attachment: {attach}")
        print("-" * 50)

    if results_filings:
        print("\n--- Detected Financial Results / Statements ---")
        for i, f in enumerate(results_filings[:3]):
            pdf_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{f['attachment']}"
            print(f"Result {i+1}:")
            print(f"  Date: {f['published_at']}")
            print(f"  Headline: {f['headline']}")
            print(f"  PDF Link: {pdf_url}")
            
            # Download the PDF
            local_filename = f"scratch/tcs_financials_{i+1}.pdf"
            os.makedirs("scratch", exist_ok=True)
            print(f"  Downloading PDF to: {local_filename}...")
            
            # Perform download with browser headers to avoid 403 blocks
            pdf_headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.bseindia.com/"
            }
            pdf_req = urllib.request.Request(pdf_url, headers=pdf_headers)
            try:
                with urllib.request.urlopen(pdf_req, timeout=30) as pdf_resp, open(local_filename, 'wb') as out_file:
                    out_file.write(pdf_resp.read())
                print(f"  Successfully downloaded: {local_filename}")
            except Exception as e:
                print(f"  Failed to download PDF: {e}")
    else:
        print("\nNo recent financial result announcements detected in the latest feed.")

if __name__ == "__main__":
    main()
