import requests

def test_nse():
    url_oc = "https://www.nseindia.com/option-chain"
    # test equities
    url_api_eq = "https://www.nseindia.com/api/option-chain-equities?symbol=RELIANCE"
    url_api_ind = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': url_oc
    }
    s = requests.Session()
    s.get(url_oc, headers=headers, timeout=10)
    
    for url in [url_api_eq, url_api_ind]:
        r = s.get(url, headers=headers, timeout=10)
        print(url, "->", r.status_code)
        if r.status_code == 200:
            print("Keys:", r.json().keys())

if __name__ == '__main__':
    test_nse()
