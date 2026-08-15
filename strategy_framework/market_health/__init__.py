"""
strategy_framework/market_health
================================
The DAILY-timeframe market-health / trend read — a slow "where are we in the
cycle" gauge, distinct from the intraday directional engine in `signals/`.

Two modules:
  * daily_bars — canonical reader for daily OHLC (price_bars timeframe='1d') plus
    the moving-average / RSI / MACD primitives. Single source for daily data.
  * trend      — assembles the 0-100 market-health score from what daily data is
    available, honestly (INSUFFICIENT_HISTORY / coverage), dropping the layers we
    have no feed for (macro, fundamentals, flows).

The MarketHealthAgent/ launcher and /api/strategy/market-health both call in here.
"""
