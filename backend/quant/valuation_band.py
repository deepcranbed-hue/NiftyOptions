"""valuation_band.py — one definition of "cheap / fair / rich" for the whole repo.

This existed in two places: `_PE_BAND` in backend/nifty50_routes.py and a copy in
backend/quant/nifty_outlook.py. Two definitions of "rich" drift, and the drift is
invisible until a scenario's exit multiple is labelled one way on the outlook tab and
the other way on the index card.

The band is a PRIOR, not a calibrated measurement — <18 cheap, 18-21 fair, 21-24 mildly
rich, >24 rich, applied to the bottom-up weighted trailing P/E of the Nifty 50. Nothing
in this repo has tested it. It is here to make a multiple readable, not to value the
index; the repo's own finding is that valuation says very little about the next three
months (H44, and the retired macro→index regression at R² = 0.036).

Stdlib only, deliberately: both a FastAPI route module and a plain script import it.
"""
from __future__ import annotations

PE_BAND = (18.0, 21.0, 24.0)


def pe_label(pe: float | None) -> str | None:
    if pe is None:
        return None
    lo, mid, hi = PE_BAND
    return ("cheap" if pe < lo else "fair" if pe < mid
            else "mildly rich" if pe < hi else "rich")
