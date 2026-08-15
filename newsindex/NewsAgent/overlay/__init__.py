"""
overlay — economics enrichment layer for the News Intelligence Agent.

Post-processes the Deterministic Core's outputs (never edits market_scan.py):
  * multi-hop transmission chains (economic priors, never Driver->Outcome direct)
  * level amplifiers for oil / USDINR / VIX
  * explicit cross-driver interaction terms
  * Energy upstream/OMC split + company->sector bridge
  * institutional terminology (Economic Prior / Supported / Dominated)
  * the live causal market graph (nodes with state/confidence/reliability/activation/news/confirmation)

Entry point: overlay.enrich.enrich_mio(mio, core).
"""
