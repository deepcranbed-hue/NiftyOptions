"""
bullion_duty.py
---------------
SINGLE SOURCE OF TRUTH for India's import duty and GST on precious metals.

Import the value from here — do NOT hardcode a duty or GST rate anywhere else in the repo
(see CLAUDE.md → DRY rule, and `exchange_config.py`, which says the same thing about lot
sizes for the same reason).

WHY THIS FILE EXISTS
--------------------
On 2026-08-18 two modules each carried their own copy of these rates, and one was already
wrong:

    probe_continuous_commodities.py   GOLD 6%   "UNVERIFIED — confirm before use"
    backend/quant/gold_cycles.py      GOLD 15%  from 2026-05-13

Both were correct when written. The duty changed on 2026-05-13 and only one copy moved. That
is the whole argument for this file: a policy rate set by someone outside this repo, read by
more than one module, cannot live in either of them. It is the same failure as C39 — a
constant that compensates for someone else's decision — except there the vendor changed the
convention and here the government changed the rate.

THE RATES MOVE, AND THEY MOVE FOR GOLD AND SILVER TOGETHER
-----------------------------------------------------------
Every Indian change in this window applied to both metals: the 2021 cut to 7.5% + 2.5% AIDC,
the 2024 budget cut to 6%, and the 2026 hike to 10% BCD + 5% AIDC. So they share a schedule
rather than getting two copies that can drift apart — which is the defect this file exists to
remove, not one to re-create at a smaller scale.

Each entry is DATED and SOURCED. When the residual basis in the Gold view starts drifting,
suspect an entry here before suspecting the market.

ASKING FOR A DATE BEFORE THE SCHEDULE STARTS IS AN ERROR, NOT A DEFAULT
-----------------------------------------------------------------------
`duty_on` raises rather than falling back to the oldest rate. A silent default is how a
2016 backtest would quietly acquire 2018's tax code and look perfectly reasonable.

    from bullion_duty import duty_on, gst_on, landed_multiplier, schedule

    duty_on("GOLD", "2026-08-18")             -> 0.15
    landed_multiplier("GOLD", "2026-08-18")   -> 1.1845
    schedule("GOLD")                          -> [(effective, rate, why, source), ...]

    python3 bullion_duty.py                   # print the table and today's multipliers
"""
from __future__ import annotations

import datetime as _dt

# GST on bullion, unchanged at 3% since 2017-07-01, which predates every series in this repo.
# Copper is an INDUSTRIAL metal and sits at the standard 18% — not a bullion rate, and the
# reason it is not simply lumped in below.
GST = {"GOLD": 0.03, "SILVER": 0.03, "COPPER": 0.18}

# Effective TOTAL import duty, inclusive of BCD + AIDC + SWS. One number, because that is
# what the landed price responds to; the components are in `why` for traceability.
_BULLION = [
    ("2014-01-01", 0.1000, "10% BCD, the setting held from 2014",
     "background rate; predates every price series here"),
    ("2019-07-06", 0.1250, "Budget 2019 — raised to curb imports",
     "gold.org/goldhub/gold-focus/2019/07/indias-gold-import-duties-rise"),
    ("2021-02-02", 0.1075, "Budget 2021 — 7.5% BCD + 2.5% AIDC, SWS exempt",
     "gold.org/goldhub/gold-focus/2021/02/indias-gold-import-duties-reduced"),
    ("2022-07-01", 0.1500, "raised to defend the current account deficit",
     "gold.org/goldhub/gold-focus/2022/07/indias-gold-import-duties-hiked"),
    ("2024-07-24", 0.0600, "Budget 2024 — 5% BCD + 1% AIDC, lowest in over a decade",
     "gold.org/goldhub/gold-focus/2024/07 ; Budget speech 23-Jul-2024, effective 24-Jul"),
    ("2026-05-13", 0.1500, "10% BCD + 5% AIDC",
     "Notifications 15-18/2026-Customs dated 12-May-2026, effective 13-May-2026"),
]

# Gold and silver share the schedule because every change above applied to both. If a future
# notification splits them, split this — do not add a second constant somewhere else.
DUTY = {"GOLD": _BULLION, "SILVER": _BULLION}

# Metals with no dated schedule. A caller may still ask for a rate, but must be told it is
# not evidence. Copper's duty has never been checked against a notification in this repo.
UNVERIFIED = {
    "COPPER": (0.05, "2026-08", "industrial metal; duty NEVER verified against a "
                                "notification — treat any COPPER landed price as indicative"),
}


def _d(day):
    return day.isoformat() if isinstance(day, (_dt.date, _dt.datetime)) else str(day)[:10]


def schedule(metal: str):
    """The dated schedule for a metal, oldest first. Empty for anything unverified."""
    return list(DUTY.get(metal.upper(), []))


def is_verified(metal: str) -> bool:
    return metal.upper() in DUTY


def duty_on(metal: str, day) -> float:
    """Total effective import duty in force on `day`.

    Raises for a date before the schedule begins rather than defaulting to the oldest rate.
    A silent default is how an old backtest quietly acquires a modern tax code.
    """
    m, day = metal.upper(), _d(day)
    if m not in DUTY:
        if m in UNVERIFIED:
            return UNVERIFIED[m][0]
        raise KeyError(f"no duty schedule for {metal!r}; known: "
                       f"{sorted(set(DUTY) | set(UNVERIFIED))}")
    rows = DUTY[m]
    if day < rows[0][0]:
        raise ValueError(
            f"{m} duty asked for {day}, before the schedule starts at {rows[0][0]}. "
            f"Extend bullion_duty.py with a sourced entry rather than assuming the "
            f"oldest rate held.")
    rate = rows[0][1]
    for eff, r, _why, _src in rows:
        if day >= eff:
            rate = r
    return rate


def gst_on(metal: str) -> float:
    try:
        return GST[metal.upper()]
    except KeyError:
        raise KeyError(f"no GST rate for {metal!r}; known: {sorted(GST)}")


def landed_multiplier(metal: str, day) -> float:
    """What international parity is multiplied by to become a landed Indian price."""
    return (1.0 + duty_on(metal, day)) * (1.0 + gst_on(metal))


def changes_between(metal: str, start, end):
    """Duty changes with an effective date inside [start, end] — for flagging a window whose
    measurement a policy step contaminates."""
    s, e = _d(start), _d(end)
    return [(eff, r, why) for eff, r, why, _ in schedule(metal) if s <= eff <= e]


if __name__ == "__main__":
    today = _dt.date.today().isoformat()
    print(f"bullion duty schedule (shared by GOLD and SILVER)\n")
    print(f"{'effective':<12}{'duty':>8}   why")
    for eff, r, why, src in _BULLION:
        print(f"{eff:<12}{r * 100:>7.2f}%   {why}\n{'':<12}{'':>8}   src: {src}")
    print(f"\ntoday = {today}")
    for m in ("GOLD", "SILVER", "COPPER"):
        try:
            print(f"  {m:<7} duty {duty_on(m, today) * 100:>6.2f}%  "
                  f"GST {gst_on(m) * 100:>5.2f}%  "
                  f"landed x{landed_multiplier(m, today):.4f}"
                  + ("" if is_verified(m) else "   <- UNVERIFIED: " + UNVERIFIED[m][2]))
        except (KeyError, ValueError) as exc:
            print(f"  {m:<7} {exc}")
