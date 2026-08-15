import re

# Fix App.tsx
with open("src/App.tsx", "r") as f:
    app = f.read()

app = app.replace("""              <StrategySuggesterPanel
                rows={analytics.chainRows}
                spot={analytics.spot}
                atmIV={analytics.atmMeta.iv}
                riskConfig={riskConfig}
                onRiskConfigChange={setRiskConfig}
                mockTrade={mockTrade}
                onMockTradeChange={setMockTrade}
                selectedOutlook={traderOutlook}
                onOutlookChange={setTraderOutlook}
                pipelineRes={pipelineRes}
              />""", """              <StrategySuggesterPanel
                rows={analytics.chainRows}
                spot={analytics.spot}
                atmIV={analytics.atmMeta.iv}
                riskConfig={riskConfig}
                onRiskConfigChange={setRiskConfig}
                mockTrade={mockTrade}
                onMockTradeChange={setMockTrade}
                selectedOutlook={traderOutlook}
                onOutlookChange={setTraderOutlook}
                pipelineRes={pipelineRes}
                optWeights={optWeights}
                setOptWeights={setOptWeights}
                optBias={optBias}
                setOptBias={setOptBias}
                optMinPop={optMinPop}
                setOptMinPop={setOptMinPop}
                optAllowUndefined={optAllowUndefined}
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
                onRunPipeline={runQuantPipeline}
              />""")
with open("src/App.tsx", "w") as f: f.write(app)


# Fix StrategySuggesterPanel.tsx
with open("src/components/StrategySuggesterPanel.tsx", "r") as f:
    panel = f.read()

# 1. Update Lucide Imports
if "Sliders" not in panel:
    panel = panel.replace("import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart } from 'lucide-react';", 
                          "import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart, Sliders, ChevronDown, ChevronRight } from 'lucide-react';")
    panel = panel.replace("import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart,Sliders } from 'lucide-react';", 
                          "import { Sparkles, Shield, AlertCircle, ArrowRight, TrendingUp, DollarSign, PieChart, Sliders, ChevronDown, ChevronRight } from 'lucide-react';")

# 2. Update Props Interface
props_interface = """  pipelineRes?: any;
  optWeights: {ev: number, pop: number, rr: number, oi: number};
  setOptWeights: (w: any) => void;
  optBias: number;
  setOptBias: (b: number) => void;
  optMinPop: number;
  setOptMinPop: (p: number) => void;
  optAllowUndefined: boolean;
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
  onRunPipeline: () => void;
}"""
if "optWeights:" not in panel:
    panel = panel.replace("  pipelineRes?: any;\n}", props_interface)

# 3. Update Component arguments
args_find = """  onOutlookChange,
  pipelineRes,
}) => {"""
args_replace = """  onOutlookChange,
  pipelineRes,
  optWeights, setOptWeights, optBias, setOptBias, optMinPop, setOptMinPop, optAllowUndefined, setOptAllowUndefined,
  optCostPerLeg, setOptCostPerLeg, optWindowPts, setOptWindowPts, optMaxWing, setOptMaxWing, optTopN, setOptTopN, optMaxLossBudget, setOptMaxLossBudget, onRunPipeline
}) => {
  const [settingsOpen, setSettingsOpen] = useState(false);"""
if "optWeights," not in panel:
    panel = panel.replace(args_find, args_replace)

# 4. Inject the Settings UI before "Strike Optimizer Output"
# First we find where to put it. The easiest place is right above:
# {/* Strike Optimizer Output */}
settings_ui = """
          {/* Settings Panel */}
          <div className="lg:col-span-2 mb-2 rounded-lg border border-slate-700/50 bg-slate-800/30 overflow-hidden">
            <button 
              onClick={() => setSettingsOpen(!settingsOpen)}
              className="w-full flex items-center justify-between p-3 bg-slate-800/80 hover:bg-slate-700/80 transition-colors"
            >
              <div className="flex items-center space-x-2 text-sm font-medium text-slate-200">
                <Sliders className="w-4 h-4 text-slate-400" />
                <span>Optimizer Settings</span>
              </div>
              {settingsOpen ? <ChevronDown className="w-4 h-4 text-slate-400"/> : <ChevronRight className="w-4 h-4 text-slate-400"/>}
            </button>
            
            {settingsOpen && (
              <div className="p-4 space-y-5 border-t border-slate-700/50 bg-slate-900/50">
                {/* Weights */}
                <div>
                  <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Objective Blending Weights</div>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                    {['ev', 'pop', 'rr', 'oi'].map((key) => (
                      <div key={key} className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-300">
                          <span className="uppercase">{key} Weight</span>
                          <span>{optWeights[key as keyof typeof optWeights].toFixed(2)}</span>
                        </div>
                        <input type="range" min="0" max="1" step="0.05"
                          value={optWeights[key as keyof typeof optWeights]}
                          onChange={e => setOptWeights({...optWeights, [key]: parseFloat(e.target.value)})}
                          className="w-full accent-indigo-500"
                        />
                      </div>
                    ))}
                  </div>
                </div>
                
                {/* Bias & Limits */}
                <div className="pt-2 border-t border-slate-700/50">
                  <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Constraints & Bias</div>
                  
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-6">
                    <div className="space-y-4">
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-300">
                          <span>Directional Bias (Tilted View)</span>
                          <span className={optBias > 0 ? "text-emerald-400 font-bold" : optBias < 0 ? "text-rose-400 font-bold" : ""}>{optBias.toFixed(2)}</span>
                        </div>
                        <input type="range" min="-1" max="1" step="0.05"
                          value={optBias}
                          onChange={e => setOptBias(parseFloat(e.target.value))}
                          className="w-full accent-purple-500"
                        />
                        <div className="flex justify-between text-[10px] text-slate-500">
                          <span>Bearish (-1)</span>
                          <span>Neutral (0)</span>
                          <span>Bullish (+1)</span>
                        </div>
                      </div>

                      <div className="space-y-1">
                        <div className="flex justify-between text-xs text-slate-300">
                          <span>Min Probability of Profit (PoP)</span>
                          <span>{optMinPop.toFixed(2)}</span>
                        </div>
                        <input type="range" min="0" max="0.95" step="0.05"
                          value={optMinPop}
                          onChange={e => setOptMinPop(parseFloat(e.target.value))}
                          className="w-full accent-emerald-500"
                        />
                      </div>
                      
                      <div className="flex items-center justify-between pt-2">
                        <span className="text-xs text-slate-300">Allow Undefined Risk (e.g. naked short strangles)</span>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input type="checkbox" className="sr-only peer" checked={optAllowUndefined} onChange={e => setOptAllowUndefined(e.target.checked)} />
                          <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-rose-500"></div>
                        </label>
                      </div>
                    </div>
                    
                    <div className="grid grid-cols-2 gap-4">
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
                          <span>Max Wing Width</span>
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
                      
                      <div className="space-y-1 col-span-2 mt-2">
                        <div className="flex justify-between text-xs text-slate-400 mb-1">
                          <span>Max Loss Budget Pts (0 = Auto from capital)</span>
                          <span className="font-bold text-amber-400">{optMaxLossBudget > 0 ? optMaxLossBudget.toFixed(0) : "Auto"}</span>
                        </div>
                        <input type="range" min="0" max="1000" step="10"
                          value={optMaxLossBudget}
                          onChange={e => setOptMaxLossBudget(parseFloat(e.target.value))}
                          className="w-full accent-amber-500"
                        />
                      </div>
                    </div>
                  </div>
                </div>
                
                <button 
                  onClick={onRunPipeline}
                  className="w-full py-3 mt-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-bold tracking-wide transition-colors shadow-lg"
                >
                  Apply & Re-Run Pipeline
                </button>
              </div>
            )}
          </div>
          {/* Strike Optimizer Output */}"""

if "Optimizer Settings" not in panel:
    panel = panel.replace("{/* Strike Optimizer Output */}", settings_ui)

with open("src/components/StrategySuggesterPanel.tsx", "w") as f:
    f.write(panel)
