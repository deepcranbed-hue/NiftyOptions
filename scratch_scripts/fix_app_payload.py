import re

with open("src/App.tsx", "r") as f:
    content = f.read()

payload_injection = """
        current_drawdown_pct: mockTrade.drawdown_pct,
        trade_max_loss_pts: mockTrade.trade_max_loss_pts,
        trade_delta: mockTrade.trade_delta,
        trade_vega: mockTrade.trade_vega,
        override_structure: mockTrade.trade_structure || undefined,
        override_is_premium_sell: mockTrade.is_premium_sell,
        opt_weights: optWeights,
        opt_bias: optBias,
        opt_min_pop: optMinPop,
        opt_allow_undefined: optAllowUndefined,
        opt_cost_per_leg: optCostPerLeg,
        opt_window_pts: optWindowPts,
        opt_max_wing: optMaxWing,
        opt_top_n: optTopN,
        opt_max_loss_budget: optMaxLossBudget,
        opt_allow_bad_rnd: optAllowBadRnd,
"""

content = content.replace("current_drawdown_pct: 0.0,", payload_injection)

with open("src/App.tsx", "w") as f:
    f.write(content)
