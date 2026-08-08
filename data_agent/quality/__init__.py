"""
data_agent.quality — IS the data good.

  data_health.py  — per (exchange, symbol, day): COVERAGE (enough bars?),
                    FREQUENCY (bars actually spaced at the symbol's expected
                    frequency — 1m by default or a per-symbol USER-DEFINED
                    frequency), and GAPS (holes). Status OK / DEGRADED /
                    WRONG_FREQ / GAPS / NO_DATA, expired instruments excluded,
                    pre-open bars filtered via a per-exchange session window.
                    Emits a human summary + sidebar alert payload. Pure/DB-only;
                    tested by test_data_health.py at the repo root.
"""
from .data_health import (  # noqa: F401
    coverage_report, alert_message, analyze_spacing,
    load_freq_config, freq_for, expected_minutes, session_minutes,
)
