import requests

def test_nse():
    url_oc = "https://www.nseindia.com/option-chain"
    url_api = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Referer': url_oc
    }
    session = requests.Session()
    try:
        res1 = session.get(url_oc, headers=headers, timeout=10)
        print("OC Status:", res1.status_code)
        print("Cookies:", session.cookies.get_dict())
        
        res2 = session.get(url_api, headers=headers, timeout=10)
        print("API Status:", res2.status_code)
        if res2.status_code == 200:
            print("API Success. Keys:", res2.json().keys())
        else:
            print("Error details:", res2.text[:200])
    except Exception as e:
        print("Exception:", str(e))

if __name__ == '__main__':
    test_nse()
