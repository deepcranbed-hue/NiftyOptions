import re

app_path = "src/App.tsx"
with open(app_path, "r") as f:
    app = f.read()

# Add states to App.tsx
state_str = """  const [optWeights, setOptWeights] = useState({ev: 0.5, pop: 0.3, rr: 0.05, oi: 0.15});
  const [optBias, setOptBias] = useState(0.0);
  const [optMinPop, setOptMinPop] = useState(0.0);
  const [optAllowUndefined, setOptAllowUndefined] = useState(false);"""
app = app.replace("  const [mockTrade, setMockTrade] = useState({", state_str + "\n\n  const [mockTrade, setMockTrade] = useState({")

# Add payload to run_pipeline
payload_str = """          override_is_premium_sell: mockTrade.is_premium_sell,
          force_news_refresh: false,
          opt_weights: optWeights,
          opt_bias: optBias,
          opt_min_pop: optMinPop,
          opt_allow_undefined: optAllowUndefined"""
app = app.replace("""          override_is_premium_sell: mockTrade.is_premium_sell,
          force_news_refresh: false""", payload_str)

# Pass props to StrategySuggesterPanel
props_str = """            <StrategySuggesterPanel
              pipelineRes={pipelineRes}
              optWeights={optWeights}
              setOptWeights={setOptWeights}
              optBias={optBias}
              setOptBias={setOptBias}
              optMinPop={optMinPop}
              setOptMinPop={setOptMinPop}
              optAllowUndefined={optAllowUndefined}
              setOptAllowUndefined={setOptAllowUndefined}
              onRunPipeline={runQuantPipeline}
            />"""
app = app.replace("""            <StrategySuggesterPanel pipelineRes={pipelineRes} />""", props_str)

with open(app_path, "w") as f:
    f.write(app)


panel_path = "src/components/StrategySuggesterPanel.tsx"
with open(panel_path, "r") as f:
    panel = f.read()

# Add imports and props interface
panel_new_imports = "import { ChevronDown, ChevronRight, Activity, TrendingUp, AlertTriangle, Info, Settings, Sliders } from 'lucide-react';"
panel = panel.replace("import { ChevronDown, ChevronRight, Activity, TrendingUp, AlertTriangle, Info } from 'lucide-react';", panel_new_imports)

props_iface = """interface StrategySuggesterPanelProps {
  pipelineRes: any;
  optWeights: {ev: number, pop: number, rr: number, oi: number};
  setOptWeights: (w: any) => void;
  optBias: number;
  setOptBias: (b: number) => void;
  optMinPop: number;
  setOptMinPop: (p: number) => void;
  optAllowUndefined: boolean;
  setOptAllowUndefined: (b: boolean) => void;
  onRunPipeline: () => void;
}

const StrategySuggesterPanel: React.FC<StrategySuggesterPanelProps> = ({ 
  pipelineRes, optWeights, setOptWeights, optBias, setOptBias, optMinPop, setOptMinPop, optAllowUndefined, setOptAllowUndefined, onRunPipeline
}) => {
  const [settingsOpen, setSettingsOpen] = React.useState(false);"""
panel = re.sub(r'const StrategySuggesterPanel: React\.FC<\{ pipelineRes: any \}> = \(\{ pipelineRes \}\) => \{', props_iface, panel)

settings_ui = """      {/* Settings Panel */}
      <div className="mb-6 rounded-lg border border-slate-700/50 bg-slate-800/30 overflow-hidden">
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
          <div className="p-4 space-y-5 border-t border-slate-700/50">
            {/* Weights */}
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Objective Blending Weights</div>
              <div className="grid grid-cols-2 gap-4">
                {['ev', 'pop', 'rr', 'oi'].map((key) => (
                  <div key={key} className="space-y-1">
                    <div className="flex justify-between text-xs text-slate-300">
                      <span className="uppercase">{key} Weight</span>
                      <span>{optWeights[key as keyof typeof optWeights].toFixed(2)}</span>
                    </div>
                    <input type="range" min="0" max="1" step="0.05"
                      value={optWeights[key as keyof typeof optWeights]}
                      onChange={e => setOptWeights({...optWeights, [key]: parseFloat(e.target.value)})}
                      className="w-full accent-blue-500"
                    />
                  </div>
                ))}
              </div>
            </div>
            
            {/* Bias & Limits */}
            <div>
              <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Constraints & Bias</div>
              <div className="space-y-4">
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-slate-300">
                    <span>Directional Bias (Tilted View)</span>
                    <span className={optBias > 0 ? "text-green-400" : optBias < 0 ? "text-red-400" : ""}>{optBias.toFixed(2)}</span>
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

                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-300">Allow Undefined Risk (e.g. naked short strangles)</span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input type="checkbox" className="sr-only peer" checked={optAllowUndefined} onChange={e => setOptAllowUndefined(e.target.checked)} />
                    <div className="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-red-500"></div>
                  </label>
                </div>
              </div>
            </div>
            
            <button 
              onClick={onRunPipeline}
              className="w-full py-2 bg-blue-600 hover:bg-blue-500 text-white rounded text-sm font-medium transition-colors"
            >
              Apply & Re-Run Pipeline
            </button>
          </div>
        )}
      </div>"""
panel = panel.replace('      {/* Top Recommendations */}', settings_ui + '\n\n      {/* Top Recommendations */}')

with open(panel_path, "w") as f:
    f.write(panel)
