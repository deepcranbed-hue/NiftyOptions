import pandas_datareader.data as web
import datetime

start = datetime.datetime(2020, 1, 1)
end = datetime.datetime(2024, 1, 1)

try:
    print("FRED: Long-Term Interest Rates for India (10-year)")
    df = web.DataReader('IRLTLT01INM156N', 'fred', start, end)
    print(df.tail())
except Exception as e:
    print(e)
    
try:
    print("FRED: Policy Repo Rate India")
    # Need to find the correct series ID for RBI Repo Rate on FRED. 
    # Or just use macrotrends?
except Exception as e:
    print(e)
