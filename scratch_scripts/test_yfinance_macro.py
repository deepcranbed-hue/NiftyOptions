import yfinance as yf
print("Trying tickers...")
for t in ['IN10Y.BO', 'IN10YT=RR', '10YIN.NS']:
    try:
        df = yf.download(t, period='1mo', progress=False)
        if not df.empty:
            print(f"Found {t}:")
            print(df.tail(1))
    except Exception as e:
        pass
