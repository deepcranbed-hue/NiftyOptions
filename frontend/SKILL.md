# Frontend UI Guide (frontend/SKILL)

## The Render-Only Contract
The React application (`src/`) is strictly a presentation and interaction layer.
- **No Client-Side Sentiment:** The function `src/lib/analytics.ts::classifyHeadline()` has been completely removed. The frontend must NEVER attempt to score natural language headlines, compute index biases, or calculate mathematical distributions.
- All quantitative modeling and AI tagging is performed by the `backend/quant/` layer. The frontend purely consumes the resulting `pipelineRes` dictionary.

## Rendered Fields per Tab

### Sector News Tab
Displays the direct results of the NLP processing from the backend.
- **Regime Banner:** Renders `pipelineRes.regime.dominant`, `conviction`, and surfaces.
- **Flip Alert:** Conditionally renders a warning if `pipelineRes.regime.flipped_from` is not null.
- **Coverage Gate:** Conditionally renders a 'Low Coverage' warning if `pipelineRes.coverage` < 0.35.
- **Sector Bias:** Renders `pipelineRes.bias` alongside the absolute numerical breakdown of `pipelineRes.sector_sentiment`.
- **Processed Headlines:** Iterates over `pipelineRes.articles` and directly displays the exact string values from the backend, including `sentiment` impact and `sectors_affected`.

### Strategy Suggester Tab
Translates the pipeline's final synthesized view into actionable parameters.
- **Comparison:** Renders `pipelineRes.comparison` to contrast the sentiment regime with the options structure.
- **Suggestion:** Renders `pipelineRes.suggestion` (e.g., specific spread recommendations).
- **RND Context:** Renders `pipelineRes.rnd` outputs including standard deviation, skew, and probabilities (below/above spot).

### Global Cues Tab
A standalone contextual region.
- Groups and manages global macroeconomic factors (e.g., US Yields, Dollar Index, Crude Oil).
- Currently driven independently of the main quant pipeline via the `pctMap` state, though it can overlay on top of the `pipelineRes`.

## Disclaimers
The UI must prominently display a disclaimer stating that the system is an experimental decision-support signal, not calibrated financial advice.
