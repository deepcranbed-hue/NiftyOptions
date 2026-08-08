"""
data_agent
==========
Self-contained package for keeping market data in sync and honest. Three concerns,
three subpackages:

    data_agent/
      constituents/ # the NIFTY-50 universe registry — one authoritative access
                    #   layer over the CSV / breeze_symbol_map.json / constituents.py
                    #   / sync / validator (files stay put), + fail-fast guard +
                    #   a Python alignment validate().
      fetching/   # WHERE data comes from — broker adapters (Breeze/Kite), the
                  #   expiry-aware instrument universe, typed F&O store, orchestrator.
      quality/    # IS the data good — 1-minute coverage + frequency checks
                  #   ("data not up to the mark") and the sidebar alert payloads.
      agent/      # the BRAIN — a local-LLM (Qwen via Ollama) control layer that
                  #   turns natural-language commands into fetch/health actions,
                  #   plus the morning/evening alert scheduler.

Design: DATA_AGENT_SPEC.md (repo root). Only `quality/data_health.py` is built and
tested so far; `fetching/` and `agent/local_llm.py` are scaffolded for the approved
build order.
"""
