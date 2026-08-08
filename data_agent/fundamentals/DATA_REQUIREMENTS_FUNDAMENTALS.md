# Fundamental Data Requirements — IT & Bank Thesis Engines (Product B)

Feeds the medium-term Fundamental/Thesis engines (§6.6/§6.7). Signal engine needs none of this.
Priority tags: **[ESSENTIAL]** (won't work without) · **[IMPORTANT]** (real lift) · **[NICE]** (later).

---

## 0. The ONE thing that matters most — point-in-time

**[ESSENTIAL] Result announcement date per quarter** — the date the numbers became *public*,
not just `period_end`. A backtest may only use data known at the prediction date; using today's
restated snapshot is look-ahead and invalidates everything. If you can capture the
announcement/result date per quarter, do it (NSE/BSE corporate-announcements have it). If not,
we approximate availability as `period_end + 45 days` — acceptable, but note it as a caveat.

## 1. Universe (cross-sectional — same fields for every name)

- **IT (~10):** TCS, INFY, HCLTECH, WIPRO, TECHM, LTIM, PERSISTENT, COFORGE, MPHASIS, LTTS
- **Bank (~12, Nifty Bank):** HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK, INDUSINDBK,
  BANKBARODA, PNB, AUBANK, IDFCFIRSTB, FEDERALBNK, BANDHANBNK

## 2. Depth & frequency

- **[ESSENTIAL] Quarterly, back to FY2015** (≥40 quarters). More is better; 2015+ captures the
  2018-20 bank NPA cycle + covid + the rate cycle — essential for a cross-*regime* fundamental test.
  Minimum acceptable FY2018, but banks especially need the pre-2020 cycle.

## 3. Common fields (BOTH sectors, per company per quarter)

- **[ESSENTIAL]** `period_end`, `announce_date`, Revenue/Total income, Net profit (PAT), EPS,
  Net worth / Book value, Shares outstanding
- **[IMPORTANT]** Dividend per share, Buyback amount (shareholder yield)
- *(We compute from these + the price series we already have: trailing P/E, P/B, EV/EBITDA,
  revenue growth YoY/QoQ, EPS growth, valuation percentile.)*

## 4. IT-specific (per company per quarter)

- **[ESSENTIAL]** Revenue (INR); **[IMPORTANT]** USD revenue (or constant-currency growth %) —
  IT's cleanest growth metric; EBIT / operating-margin %
- **[NICE]** Attrition %, Deal TCV / order book, Headcount, Net cash — usually only in concalls,
  hard to structure; skip for v1

## 5. Bank-specific (per bank per quarter) — different financials entirely

- **[ESSENTIAL]** Net Interest Income (NII), **NIM %**, Gross NPA %, Net NPA %, Advances (loans),
  Deposits, ROA %
- **[IMPORTANT]** Provisions / credit cost, CASA %, PPOP (pre-provision op profit)
- **[NICE]** Capital adequacy (CAR), slippages
- *(Banks are valued on **P/B**, not P/E — book value above is essential.)*

## 6. Macro — India rate cycle (for the Bank thesis; daily or monthly, deep)

- **[ESSENTIAL]** India 10-Year G-Sec yield (the core bank driver — we only have *US* 10Y today),
  RBI repo rate
- **[IMPORTANT]** India 3M/1Y T-bill (→ yield-curve slope), system credit growth % (RBI, monthly)
- **[NICE]** CPI inflation (monthly)

## 7. Format & sources

- **Format:** one row per `(symbol, period_end)` with `announce_date` + the fields — wide CSV, or
  long `(symbol, period_end, announce_date, metric, value)` (matches the existing
  `fundamentals.financials` long schema). Macro: `(date, series, value)`.
- **Sources:** screener.in (≈10yr quarterly + ratios — good depth, usually *no* announce date →
  approximate with lag); NSE/BSE corporate-announcements (announce dates); RBI/DBIE (India rates,
  credit growth). Capitaline/CMIE only if you want institutional-grade point-in-time.

## 8. What "done" looks like (re-run the audit to confirm)

`fundamental_data_audit.py` should flip to **READY**: median ≥20 quarters/company, cross-sectional
N ≥200, coverage ≥8/10. Then: cross-sectional base model (1-2 factors → forward 60/120-day
rank-IC, per-regime) → add factors one at a time → software regime / evidence policy last.
