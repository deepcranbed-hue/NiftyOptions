# Quant Module Details (backend/quant/SKILL)

## Execution Order (`run_pipeline`)
The pipeline runs in a strict sequential order:
0. **Complacency & Risk:** `complacency.py` and `risk_budget.py` act as the chain gauge and risk gate, respectively.
1. **Regime Assessment:** `assess_regime()` identifies the dominant sentiment driver from the latest news window.
2. **Sector Sentiment Aggregation:** `sector_sentiment_from_gemini()` groups raw articles by Gemini-tagged canonical sectors.
3. **Index Bias & Coverage:** `index_bias()` maps sector sentiment against NIFTY50 `sector_weights()` to compute a net float and % coverage.
4. **News Momentum:** Momentum scaling (0.0 to 1.0) is heuristically derived from conviction and corroborating news surfaces.
5. **RND Extraction:** `extract_rnd()` runs the raw option chain through the scipy trapezoid rule to generate the `grid` and `dens`.
6. **Market View Synthesis:** `compare_with_regime()` and `suggest_strategy()` finalize the output based on regime vs bias.

## Required Caller Inputs
When calling `run_pipeline(articles, chain, weights=None, prev_regime=None)`, the caller must supply:
- **`articles`**: A list of dictionaries, each containing at minimum `sentiment` (float) and `sectors_affected` (list of strings).
- **`chain`**: A dictionary containing `strikes`, `call_ltp`, `put_ltp`, `spot`, and `days`.
- **`prev_regime`** (Optional): A string representing the previous regime state to enable hysteresis.

## Mathematical Caveats
- **RND Trapezoid Integration:** `rnd.py` uses `scipy.integrate.trapezoid`. The algorithm calculates valid skew *only* when Out-Of-The-Money (OTM) Put prices are provided. Do NOT use call-put parity to synthesize missing puts if the market is illiquid.
- **Sector Source of Truth:** `sector_map.py` is the absolute source of truth. If a Gemini tag does not match a key in `sector_map.py`, its weighting is effectively zeroed (or grouped to OTHER).
- **Pseudo-Probability:** The `prob_up` calculation in `decision_engine.py` is a heuristic pseudo-probability mapping. It is not an empirically calibrated win rate.
- **Global Cues Timing:** `global_cues.py` provides EOD comparisons and intraday futures momentum, but it sits *outside* the main option flow pipeline.

## Validation Commands
- **Sector Map Verification:** `python backend/quant/sector_map.py` (Smoke tests canonical sectors to ensure 100% NIFTY weighting without orphans).
- **RND Debugging:** `python debug_rnd.py` (or `rnd_check.diagnose`).
- **Pipeline Harness Evaluation:** `python -m backend.quant.harness evaluate` (Runs the historical offline evaluation).
