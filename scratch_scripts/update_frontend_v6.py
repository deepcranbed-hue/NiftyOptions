with open("src/components/StrategySuggesterPanel.tsx", "r") as f:
    content = f.read()

vol_panel = """
          {/* Vol Attribution Panel */}
          {pipelineRes.vol_attribution && (
            <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col mb-4">
              <h3 className="text-sm font-bold uppercase tracking-wider text-slate-500 mb-4 flex items-center gap-2">
                <span>Volatility Attribution</span>
              </h3>
              
              <div className="flex items-center gap-4 mb-4">
                <div className="flex flex-col">
                  <span className="text-[10px] uppercase text-slate-400 font-bold">Chain ATM IV</span>
                  <span className="text-xl font-black text-slate-800">{pipelineRes.vol_attribution.chain_atm_iv_pct}%</span>
                </div>
                <div className="h-8 w-px bg-slate-200"></div>
                <div className="flex flex-col">
                  <span className="text-[10px] uppercase text-slate-400 font-bold">India VIX (News)</span>
                  <span className="text-xl font-black text-slate-800">{pipelineRes.vol_attribution.india_vix ? `${pipelineRes.vol_attribution.india_vix}%` : 'N/A'}</span>
                </div>
                {pipelineRes.vol_attribution.iv_vs_vix_gap && (
                  <>
                    <div className="h-8 w-px bg-slate-200"></div>
                    <div className="flex flex-col">
                      <span className="text-[10px] uppercase text-slate-400 font-bold">Gap</span>
                      <span className="text-xl font-black text-amber-500">+{pipelineRes.vol_attribution.iv_vs_vix_gap}%</span>
                    </div>
                  </>
                )}
              </div>

              <div className="space-y-3 mt-2">
                {pipelineRes.vol_attribution.causes.map((c: any, i: number) => (
                  <div key={i} className={`p-3 rounded-lg border ${c.harvestable === true ? 'bg-emerald-50 border-emerald-200' : 'bg-rose-50 border-rose-200'}`}>
                    <div className="flex items-center justify-between mb-1">
                      <span className={`text-xs font-bold uppercase tracking-wider ${c.harvestable === true ? 'text-emerald-700' : 'text-rose-700'}`}>
                        {c.cause.replace(/_/g, ' ')}
                      </span>
                      <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${c.harvestable === 'conditionally' ? 'bg-amber-100 text-amber-800' : c.harvestable === true ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>
                        {c.harvestable === 'conditionally' ? 'TRAP / CONDITIONALLY HARVESTABLE' : c.harvestable === true ? 'HARVESTABLE' : 'TRAP'}
                      </span>
                    </div>
                    <p className="text-sm text-slate-700 mb-2">{c.detail}</p>
                    {c.warning && <p className="text-xs font-medium text-slate-500 italic">"{c.warning}"</p>}
                  </div>
                ))}
              </div>

              <div className={`mt-4 p-3 rounded-lg border ${pipelineRes.vol_attribution.sell_premium_verdict.startsWith('CAUTION') ? 'bg-rose-50 border-rose-200 text-rose-800' : 'bg-emerald-50 border-emerald-200 text-emerald-800'} text-sm font-semibold`}>
                {pipelineRes.vol_attribution.sell_premium_verdict}
              </div>
            </div>
          )}
"""

if "Vol Attribution Panel" not in content:
    content = content.replace(
        "          {/* Settings Panel */}",
        vol_panel + "\n          {/* Settings Panel */}"
    )

vol_caution = """
                    {strat.vol_caution && (
                      <div className="mt-3 p-2 bg-rose-500/10 border border-rose-500/30 rounded-lg flex gap-2 items-start">
                        <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                        <div className="text-xs text-rose-200 font-medium leading-tight">
                          {strat.vol_caution}
                        </div>
                      </div>
                    )}
"""

if "strat.vol_caution" not in content:
    content = content.replace(
        "                    <div className=\"grid grid-cols-2 md:grid-cols-5 gap-3 mt-4\">",
        vol_caution + "\n                    <div className=\"grid grid-cols-2 md:grid-cols-5 gap-3 mt-4\">"
    )

with open("src/components/StrategySuggesterPanel.tsx", "w") as f:
    f.write(content)
