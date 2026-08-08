-- ============================================================================
-- Sector Fundamental Scorecard — analytical views over fundamentals.*
-- ============================================================================
-- Per-company building blocks. sector_scorecard.py rolls these up to a sector
-- read. All read-only views; safe to re-create. Uses ONLY data the loader
-- populates (no forward estimates — guidance lives in the NewsAgent layer).
-- ============================================================================
SET search_path TO fundamentals, public;

-- Drop first, in dependency order. CREATE OR REPLACE VIEW fails when a view's
-- output columns change (Postgres: "cannot drop columns from view"), which would
-- silently leave an old definition in place.
DROP VIEW IF EXISTS fundamentals.v_company_scorecard;
DROP VIEW IF EXISTS fundamentals.v_company_growth;
DROP VIEW IF EXISTS fundamentals.v_company_ratios;
DROP VIEW IF EXISTS fundamentals.v_company_fii;

-- --- revenue & net-profit growth: QoQ (consecutive quarters, summary) and
-- --- YoY (annual points that live in the full/fs=true section).
-- Upstox's quarterly 'summary' holds only ~4 consecutive quarters (good for QoQ);
-- the 'full' section carries annual-spaced points (Mar-YYYY) -> use for YoY.
CREATE OR REPLACE VIEW fundamentals.v_company_growth AS
WITH q AS (   -- consecutive quarters -> QoQ
    SELECT isin, line_item AS metric, period_end, value,
           LAG(value) OVER (PARTITION BY isin, line_item ORDER BY period_end) AS prev,
           ROW_NUMBER() OVER (PARTITION BY isin, line_item ORDER BY period_end DESC) AS rn
    FROM fundamentals.financials
    WHERE statement = 'income' AND time_period = 'quarterly' AND section = 'summary'
      AND line_item IN ('revenue', 'net_profit')
),
y AS (        -- annual points in the full section -> YoY
    SELECT isin,
           CASE WHEN line_item = 'revenue' THEN 'revenue'
                WHEN line_item = 'net_profit' THEN 'net_profit' END AS metric,
           period_end, value,
           LAG(value) OVER (PARTITION BY isin, line_item ORDER BY period_end) AS prev,
           ROW_NUMBER() OVER (PARTITION BY isin, line_item ORDER BY period_end DESC) AS rn
    FROM fundamentals.financials
    WHERE statement = 'income' AND time_period = 'yearly' AND section = 'full'
      AND line_item IN ('revenue', 'net_profit')
)
SELECT c.isin, c.symbol, m.metric AS line_item,
       ROUND((qq.value - qq.prev) / NULLIF(qq.prev, 0) * 100, 2) AS qoq_pct,
       ROUND((yy.value - yy.prev) / NULLIF(yy.prev, 0) * 100, 2) AS yoy_pct
FROM fundamentals.companies c
CROSS JOIN (VALUES ('revenue'), ('net_profit')) m(metric)
LEFT JOIN q qq ON qq.isin = c.isin AND qq.metric = m.metric AND qq.rn = 1
LEFT JOIN y yy ON yy.isin = c.isin AND yy.metric = m.metric AND yy.rn = 1;

-- --- latest key ratios, pivoted (company values; sector_value is unreliable) -
CREATE OR REPLACE VIEW fundamentals.v_company_ratios AS
SELECT c.isin, c.symbol,
       MAX(k.company_value) FILTER (WHERE k.ratio = 'P/E')       AS pe,
       MAX(k.company_value) FILTER (WHERE k.ratio = 'P/B')       AS pb,
       MAX(k.company_value) FILTER (WHERE k.ratio = 'ROE')       AS roe,
       MAX(k.company_value) FILTER (WHERE k.ratio = 'ROCE')      AS roce,
       MAX(k.company_value) FILTER (WHERE k.ratio = 'EV/EBITDA') AS ev_ebitda
FROM fundamentals.companies c
JOIN fundamentals.key_ratios k ON k.isin = c.isin
WHERE k.as_of = (SELECT MAX(as_of) FROM fundamentals.key_ratios k2 WHERE k2.isin = c.isin)
GROUP BY c.isin, c.symbol;

-- --- FII holding: latest vs ~3 quarters ago (accumulation/distribution) ------
CREATE OR REPLACE VIEW fundamentals.v_company_fii AS
WITH s AS (
    SELECT isin, period_end, pct,
           ROW_NUMBER() OVER (PARTITION BY isin ORDER BY period_end DESC) AS rn
    FROM fundamentals.shareholding WHERE category = 'fii'
)
SELECT a.isin, a.pct AS fii_latest, b.pct AS fii_prior,
       ROUND(a.pct - b.pct, 2) AS fii_delta
FROM s a LEFT JOIN s b ON a.isin = b.isin AND b.rn = 4
WHERE a.rn = 1;

-- --- one row per company: everything the scorecard needs --------------------
CREATE OR REPLACE VIEW fundamentals.v_company_scorecard AS
SELECT c.isin, c.symbol, p.sector,
       r.pe, r.pb, r.roe, r.roce, r.ev_ebitda,
       gr.yoy_pct AS rev_yoy, gr.qoq_pct AS rev_qoq,
       gn.yoy_pct AS np_yoy,  gn.qoq_pct AS np_qoq,
       fi.fii_latest, fi.fii_delta
FROM fundamentals.companies c
LEFT JOIN fundamentals.company_profile p ON p.isin = c.isin
LEFT JOIN fundamentals.v_company_ratios r ON r.isin = c.isin
LEFT JOIN fundamentals.v_company_growth gr ON gr.isin = c.isin AND gr.line_item = 'revenue'
LEFT JOIN fundamentals.v_company_growth gn ON gn.isin = c.isin AND gn.line_item = 'net_profit'
LEFT JOIN fundamentals.v_company_fii fi ON fi.isin = c.isin;
