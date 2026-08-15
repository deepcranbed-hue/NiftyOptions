with open("src/components/StrategySuggesterPanel.tsx", "r") as f:
    code = f.read()

# Add optAllowBadRnd state
if "optAllowBadRnd" not in code:
    code = code.replace(
        "const [optAllowUndefined, setOptAllowUndefined] = useState(false);",
        "const [optAllowUndefined, setOptAllowUndefined] = useState(false);\n  const [optAllowBadRnd, setOptAllowBadRnd] = useState(false);"
    )

# Add opt_allow_bad_rnd to API payload
if "opt_allow_bad_rnd:" not in code:
    code = code.replace(
        "opt_max_loss_budget: optMaxLossBudget",
        "opt_max_loss_budget: optMaxLossBudget,\n          opt_allow_bad_rnd: optAllowBadRnd"
    )

# Add checkbox UI
checkbox_ui = """
                      <div className="flex items-center justify-between pt-2">
                        <span className="text-xs text-slate-300">Allow Bad RND (ignore calibration)</span>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input type="checkbox" className="sr-only peer" checked={optAllowBadRnd} onChange={e => setOptAllowBadRnd(e.target.checked)} />
                          <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-rose-500"></div>
                        </label>
                      </div>
"""
if "Allow Bad RND" not in code:
    code = code.replace(
        "<span>Allow Undefined Risk",
        checkbox_ui + "\n                      <div className=\"flex items-center justify-between pt-2\">\n                        <span className=\"text-xs text-slate-300\">Allow Undefined Risk"
    )

# Add uncalibrated warning UI
warning_ui = """
          {pipelineRes.optimizer && pipelineRes.optimizer.status === 'rnd_uncalibrated' && (
            <div className="lg:col-span-2 bg-rose-950/30 rounded-2xl p-6 border border-rose-900 shadow-xl text-white">
              <h3 className="text-sm font-bold uppercase tracking-wider text-rose-400 mb-2">⚠ Optimizer Blocked</h3>
              <p className="text-sm text-slate-300">{pipelineRes.optimizer.rnd_warning}</p>
              <p className="text-xs text-slate-400 mt-4">You can bypass this by checking "Allow Bad RND" in the settings.</p>
            </div>
          )}
"""
if "rnd_uncalibrated" not in code:
    code = code.replace(
        "{/* Strike Optimizer Output */}",
        "{/* Strike Optimizer Output */}\n" + warning_ui
    )

with open("src/components/StrategySuggesterPanel.tsx", "w") as f:
    f.write(code)
