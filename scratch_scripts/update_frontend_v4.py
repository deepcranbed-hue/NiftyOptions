import re

# --- backend/main.py ---
main_path = "backend/main.py"
with open(main_path, "r") as f:
    main_code = f.read()

main_code = main_code.replace(
"""    opt_min_pop: float = 0.0
    opt_allow_undefined: bool = False""",
"""    opt_min_pop: float = 0.0
    opt_allow_undefined: bool = False
    opt_cost_per_leg: float = 20.0
    opt_window_pts: int = 500
    opt_max_wing: int = 300
    opt_top_n: int = 6
    opt_max_loss_budget: float = 0.0""")

main_code = main_code.replace(
"""            opt_bias=req.opt_bias,
            opt_min_pop=req.opt_min_pop,
            opt_allow_undefined=req.opt_allow_undefined
        )""",
"""            opt_bias=req.opt_bias,
            opt_min_pop=req.opt_min_pop,
            opt_allow_undefined=req.opt_allow_undefined,
            opt_cost_per_leg=req.opt_cost_per_leg,
            opt_window_pts=req.opt_window_pts,
            opt_max_wing=req.opt_max_wing,
            opt_top_n=req.opt_top_n,
            opt_max_loss_budget=req.opt_max_loss_budget
        )""")
with open(main_path, "w") as f:
    f.write(main_code)


# --- backend/quant/pipeline.py ---
pipe_path = "backend/quant/pipeline.py"
with open(pipe_path, "r") as f:
    pipe_code = f.read()

pipe_code = pipe_code.replace(
"""                 opt_bias: float | None = None,
                 opt_min_pop: float = 0.0,
                 opt_allow_undefined: bool = False) -> dict:""",
"""                 opt_bias: float | None = None,
                 opt_min_pop: float = 0.0,
                 opt_allow_undefined: bool = False,
                 opt_cost_per_leg: float = 20.0,
                 opt_window_pts: int = 500,
                 opt_max_wing: int = 300,
                 opt_top_n: int = 6,
                 opt_max_loss_budget: float = 0.0) -> dict:""")

pipe_code = pipe_code.replace(
"""        weights=opt_weights,
        max_loss_budget_pts=(cfg.capital * cfg.risk_per_trade_pct) / cfg.lot_size if cfg else 100,
        min_pop=opt_min_pop,
        cost_per_leg_pts=20.0, # Approx cost per leg in pts
        allow_undefined=opt_allow_undefined,
        oi_weight=opt_weights.get("oi", 0.0) if opt_weights else 0.0,
        bias=opt_bias
    )""",
"""        weights=opt_weights,
        max_loss_budget_pts=opt_max_loss_budget if opt_max_loss_budget > 0 else ((cfg.capital * cfg.risk_per_trade_pct) / cfg.lot_size if cfg else 100),
        min_pop=opt_min_pop,
        cost_per_leg_pts=opt_cost_per_leg,
        window_pts=opt_window_pts,
        max_wing=opt_max_wing,
        allow_undefined=opt_allow_undefined,
        oi_weight=opt_weights.get("oi", 0.0) if opt_weights else 0.0,
        top_n=opt_top_n,
        bias=opt_bias
    )""")
with open(pipe_path, "w") as f:
    f.write(pipe_code)


# --- src/App.tsx ---
app_path = "src/App.tsx"
with open(app_path, "r") as f:
    app = f.read()

app = app.replace(
"""  const [optMinPop, setOptMinPop] = useState(0.0);
  const [optAllowUndefined, setOptAllowUndefined] = useState(false);""",
"""  const [optMinPop, setOptMinPop] = useState(0.0);
  const [optAllowUndefined, setOptAllowUndefined] = useState(false);
  const [optCostPerLeg, setOptCostPerLeg] = useState(20.0);
  const [optWindowPts, setOptWindowPts] = useState(500);
  const [optMaxWing, setOptMaxWing] = useState(300);
  const [optTopN, setOptTopN] = useState(6);
  const [optMaxLossBudget, setOptMaxLossBudget] = useState(0.0);""")

app = app.replace(
"""          opt_bias: optBias,
          opt_min_pop: optMinPop,
          opt_allow_undefined: optAllowUndefined
        })""",
"""          opt_bias: optBias,
          opt_min_pop: optMinPop,
          opt_allow_undefined: optAllowUndefined,
          opt_cost_per_leg: optCostPerLeg,
          opt_window_pts: optWindowPts,
          opt_max_wing: optMaxWing,
          opt_top_n: optTopN,
          opt_max_loss_budget: optMaxLossBudget
        })""")

app = app.replace(
"""              optAllowUndefined={optAllowUndefined}
              setOptAllowUndefined={setOptAllowUndefined}
              onRunPipeline={runQuantPipeline}""",
"""              optAllowUndefined={optAllowUndefined}
              setOptAllowUndefined={setOptAllowUndefined}
              optCostPerLeg={optCostPerLeg}
              setOptCostPerLeg={setOptCostPerLeg}
              optWindowPts={optWindowPts}
              setOptWindowPts={setOptWindowPts}
              optMaxWing={optMaxWing}
              setOptMaxWing={setOptMaxWing}
              optTopN={optTopN}
              setOptTopN={setOptTopN}
              optMaxLossBudget={optMaxLossBudget}
              setOptMaxLossBudget={setOptMaxLossBudget}
              onRunPipeline={runQuantPipeline}""")
with open(app_path, "w") as f:
    f.write(app)


# --- src/components/StrategySuggesterPanel.tsx ---
import re
panel_path = "src/components/StrategySuggesterPanel.tsx"
with open(panel_path, "r") as f:
    panel = f.read()

props = """  optAllowUndefined: boolean;
  setOptAllowUndefined: (b: boolean) => void;
  optCostPerLeg: number;
  setOptCostPerLeg: (n: number) => void;
  optWindowPts: number;
  setOptWindowPts: (n: number) => void;
  optMaxWing: number;
  setOptMaxWing: (n: number) => void;
  optTopN: number;
  setOptTopN: (n: number) => void;
  optMaxLossBudget: number;
  setOptMaxLossBudget: (n: number) => void;
  onRunPipeline: () => void;"""
panel = panel.replace('  optAllowUndefined: boolean;\n  setOptAllowUndefined: (b: boolean) => void;\n  onRunPipeline: () => void;', props)

args_find = "  pipelineRes, optWeights, setOptWeights, optBias, setOptBias, optMinPop, setOptMinPop, optAllowUndefined, setOptAllowUndefined, onRunPipeline\n}) => {"
args_replace = """  pipelineRes, optWeights, setOptWeights, optBias, setOptBias, optMinPop, setOptMinPop, optAllowUndefined, setOptAllowUndefined, 
  optCostPerLeg, setOptCostPerLeg, optWindowPts, setOptWindowPts, optMaxWing, setOptMaxWing, optTopN, setOptTopN, optMaxLossBudget, setOptMaxLossBudget, onRunPipeline
}) => {"""
panel = panel.replace(args_find, args_replace)

new_ui = """                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-300">Allow Undefined Risk (e.g. naked short strangles)</span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={optAllowUndefined} onChange={e => setOptAllowUndefined(e.target.checked)} />
                    <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-red-500"></div>
                  </label>
                </div>

                <div className="grid grid-cols-2 gap-4 pt-2 border-t border-slate-700/50">
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-400">
                      <span>Cost per Leg (pts)</span>
                      <span>{optCostPerLeg.toFixed(1)}</span>
                    </div>
                    <input type="range" min="0" max="50" step="1"
                      value={optCostPerLeg}
                      onChange={e => setOptCostPerLeg(parseFloat(e.target.value))}
                      className="w-full accent-blue-500"
                    />
                  </div>
                  
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-400">
                      <span>Search Window (pts)</span>
                      <span>{optWindowPts}</span>
                    </div>
                    <input type="range" min="100" max="2000" step="50"
                      value={optWindowPts}
                      onChange={e => setOptWindowPts(parseInt(e.target.value))}
                      className="w-full accent-blue-500"
                    />
                  </div>
                  
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-400">
                      <span>Max Wing Width (pts)</span>
                      <span>{optMaxWing}</span>
                    </div>
                    <input type="range" min="50" max="1000" step="50"
                      value={optMaxWing}
                      onChange={e => setOptMaxWing(parseInt(e.target.value))}
                      className="w-full accent-blue-500"
                    />
                  </div>
                  
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-400">
                      <span>Top N Results</span>
                      <span>{optTopN}</span>
                    </div>
                    <input type="range" min="1" max="20" step="1"
                      value={optTopN}
                      onChange={e => setOptTopN(parseInt(e.target.value))}
                      className="w-full accent-blue-500"
                    />
                  </div>
                  
                  <div className="space-y-1 col-span-2">
                    <div className="flex justify-between text-xs text-slate-400">
                      <span>Max Loss Budget Pts (0 = Auto from capital)</span>
                      <span>{optMaxLossBudget > 0 ? optMaxLossBudget.toFixed(0) : "Auto"}</span>
                    </div>
                    <input type="range" min="0" max="1000" step="10"
                      value={optMaxLossBudget}
                      onChange={e => setOptMaxLossBudget(parseFloat(e.target.value))}
                      className="w-full accent-blue-500"
                    />
                  </div>
                </div>"""

panel = panel.replace("""                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-300">Allow Undefined Risk (e.g. naked short strangles)</span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={optAllowUndefined} onChange={e => setOptAllowUndefined(e.target.checked)} />
                    <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-red-500"></div>
                  </label>
                </div>""", new_ui)

with open(panel_path, "w") as f:
    f.write(panel)
