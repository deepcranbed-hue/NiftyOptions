"""
strategy_framework/backtest/metrics.py
======================================
Trade-log summary statistics for the walk-forward backtest.

Deliberately honest: with only a handful of sessions of history these numbers
are descriptive, not inferential. Every summary carries `n_trades` and a
`sufficient` flag (per DECISIONS.md D-MA-04, real edge estimation needs >= ~60
sessions). Treat anything below that as a smoke test of the plumbing.
"""
from __future__ import annotations
import numpy as np

from ..config.settings import LOT_SIZE   # single source of truth (exchange_config)

MIN_SESSIONS_FOR_EDGE = 60


def mps0_max_profit(prices: list[float], flip_cost_inr: float, lot_size: int) -> float:
    """Perfect-hindsight Maximum Profit Strategy (Salov MPS0) on a price path, in ₹.

    A 1-lot reversal trader that is always in the market and flips long<->short at
    the best possible ticks, paying `flip_cost_inr` per reversal. Computed with an
    O(n) two-state DP (best PnL ending long vs short). This is the UNACHIEVABLE
    ceiling (it looks ahead) — used only to normalise realised PnL into a
    "% of max profit captured" skill score, per the paper's use of MPS as a
    *measure* of available opportunity, not a tradeable signal.
    """
    if not prices or len(prices) < 2:
        return 0.0
    long_pnl = short_pnl = 0.0        # free initial entry either way
    for i in range(1, len(prices)):
        d = (prices[i] - prices[i - 1]) * lot_size
        new_long = max(long_pnl, short_pnl - flip_cost_inr) + d
        new_short = max(short_pnl, long_pnl - flip_cost_inr) - d
        long_pnl, short_pnl = new_long, new_short
    return max(long_pnl, short_pnl)


def _attribution(trades: list[dict], key: str, default: str = "unknown") -> dict:
    """Group net P&L by a trade field (e.g. ``exit_reason``, ``entry_family``).

    Answers "which exit rule / which structure family is making or losing money?"
    — the two questions the single pooled hit-rate hides. Returns
    ``{value: {n, wins, hit_rate, net_rupees, net_pts, avg_pts, cost_inr}}`` sorted
    by ``net_rupees`` ASCENDING, so the biggest bleeders surface first. Trades that
    lack the field bucket under ``default`` (e.g. book-position trades with no
    ``exit_reason``). DESCRIPTIVE ONLY — same D-MA-04 caveat as the parent summary.
    """
    groups: dict[str, list] = {}
    for t in trades:
        groups.setdefault(str(t.get(key, default)), []).append(t)
    out: dict[str, dict] = {}
    for val, ts in groups.items():
        pnl = np.array([t.get("pnl_pts", 0.0) for t in ts], float)
        rup = np.array([t.get("pnl_rupees", 0.0) for t in ts], float)
        cost = np.array([t.get("cost_inr", 0.0) for t in ts], float)
        wins = pnl > 0
        out[val] = {
            "n": len(ts),
            "wins": int(wins.sum()),
            "hit_rate": round(float(wins.mean()), 4),
            "net_rupees": round(float(rup.sum()), 0),
            "net_pts": round(float(pnl.sum()), 2),
            "avg_pts": round(float(pnl.mean()), 3),
            "cost_inr": round(float(cost.sum()), 0),
        }
    return dict(sorted(out.items(), key=lambda kv: kv[1]["net_rupees"]))


def _max_consecutive_loss(trades: list[dict]) -> int:
    """Longest run of consecutive losing trades (net of costs), in list order.

    The trade list is built in entry-snapshot order, so this is the worst losing
    streak a desk would actually have felt — the risk stat a plain max-drawdown
    number does not convey.
    """
    mx = cur = 0
    for t in trades:
        if float(t.get("pnl_rupees", 0.0)) < 0:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    return mx


def summarize(trades: list[dict], price_path: list[float] | None = None,
              mps_flip_cost_inr: float | None = None, lot_size: int = LOT_SIZE,
              mps_label: str = "") -> dict:
    """trades: list of {pnl_pts, pnl_rupees, direction, family, won, ...}.

    If `price_path` (the underlying spot series over the window) and a
    `mps_flip_cost_inr` are supplied, also report the MPS0 perfect-hindsight
    ceiling and the fraction of it the strategy captured.
    """
    def _mps_block(net_rupees):
        if price_path is None or mps_flip_cost_inr is None or len(price_path) < 2:
            return {}
        mps = mps0_max_profit(price_path, mps_flip_cost_inr, lot_size)
        cap = (net_rupees / mps) if mps > 1e-9 else None
        return {"mps0_max_rupees": round(mps, 0),
                "mps0_flip_cost_inr": round(mps_flip_cost_inr, 0),
                "mps0_basis": mps_label or "custom",
                "capture_pct": round(100 * cap, 1) if cap is not None else None,
                "mps0_note": ("Perfect-hindsight ceiling (looks ahead) — capture_pct = "
                              "realised net ÷ MPS0. A skill score vs the opportunity that "
                              "existed, NOT an achievable target. Descriptive (D-MA-04).")}

    if not trades:
        out = {"n_trades": 0, "sufficient": False,
               "note": "no trades taken (gates filtered everything)"}
        out.update(_mps_block(0.0))
        return out

    pnl = np.array([t["pnl_pts"] for t in trades], float)
    rup = np.array([t.get("pnl_rupees", 0.0) for t in trades], float)
    cost = np.array([t.get("cost_inr", 0.0) for t in trades], float)
    wins = pnl > 0
    n = len(pnl)

    gross_win = pnl[wins].sum() if wins.any() else 0.0
    gross_loss = -pnl[~wins].sum() if (~wins).any() else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 1e-9 else float("inf")

    # equity curve / max drawdown in points
    eq = np.cumsum(pnl)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = float(dd.min()) if n else 0.0

    # per-trade sharpe-ish (not annualised — too little data to annualise)
    sharpe_like = float(pnl.mean() / (pnl.std() + 1e-9)) if n > 1 else 0.0

    # REALIZED CVaR of trade P&L (₹): mean of the worst q-tail of trades — the
    # ex-post counterpart to risk_forecast's ex-ante CVaR10. Reported only with
    # n >= 10; on fewer trades the "tail" is just the single worst trade dressed up.
    def _cvar(q):
        if n < 10:
            return None
        k = max(1, int(np.ceil(n * q)))
        return round(float(np.sort(rup)[:k].mean()), 0)
    cvar10_inr, cvar05_inr = _cvar(0.10), _cvar(0.05)

    sessions = len({t.get("session") for t in trades if t.get("session")})
    net_rupees = float(rup.sum())

    return {
        **_mps_block(net_rupees),
        "n_trades": n,
        "n_sessions": sessions,
        "sufficient": sessions >= MIN_SESSIONS_FOR_EDGE,
        "hit_rate": round(float(wins.mean()), 4),
        "avg_pnl_pts": round(float(pnl.mean()), 3),
        "total_pnl_pts": round(float(pnl.sum()), 2),
        "total_pnl_rupees": round(float(rup.sum()), 0),          # net of costs
        "total_cost_inr": round(float(cost.sum()), 0),
        "gross_pnl_rupees": round(float(rup.sum() + cost.sum()), 0),  # before costs
        "avg_cost_per_trade_inr": round(float(cost.mean()), 0) if n else 0.0,
        "best_pts": round(float(pnl.max()), 2),
        "worst_pts": round(float(pnl.min()), 2),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "sharpe_like_per_trade": round(sharpe_like, 3),
        "max_drawdown_pts": round(max_dd, 2),
        "cvar10_inr": cvar10_inr,           # mean of worst 10% of trades (₹, needs n≥10)
        "cvar05_inr": cvar05_inr,           # mean of worst 5% of trades (₹, needs n≥10)
        "max_consecutive_loss": _max_consecutive_loss(trades),
        # P&L attribution — which exit rule / which structure family earns or bleeds.
        "by_exit_reason": _attribution(trades, "exit_reason"),
        "by_family": _attribution(trades, "entry_family"),
        "note": ("DESCRIPTIVE ONLY — %d sessions < %d needed for edge inference "
                 "(D-MA-04)" % (sessions, MIN_SESSIONS_FOR_EDGE))
                if sessions < MIN_SESSIONS_FOR_EDGE else "sufficient history",
    }
