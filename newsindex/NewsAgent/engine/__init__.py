"""Vendored copy of the NiftyOptions market_scan engine, so NewsAgent runs
standalone (no dependency on the parent newsindex/ project).

Source: newsindex/market_scan.py (+ fetch_article, desk_note_examples, build_events,
signals.json, events.db), copied verbatim. Refresh coefficients by running the vendored
build_events.py here, or re-copy from the parent when the engine is updated.
"""
