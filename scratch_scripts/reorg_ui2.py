import re

with open("src/App.tsx", "r") as f:
    code = f.read()

# Add react-router-dom imports
if "react-router-dom" not in code:
    code = code.replace(
        "import React, { useState, useMemo } from 'react';",
        "import React, { useState, useMemo, useEffect } from 'react';\nimport { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';"
    )

start_marker = "{/* Tab Selection Navigation */}"
end_marker = "{/* AI Copilot Modal */}"

start_idx = code.find(start_marker)
end_idx = code.find(end_marker)

new_ui = """{/* NEW WORKSPACE SHELL */}
        <div className="flex flex-col md:flex-row gap-6">
          {/* Left Rail */}
          <div className="w-full md:w-56 shrink-0 space-y-2">
            
            {/* WORKSPACE NAV */}
            <div className="bg-slate-900 text-white p-2 rounded-2xl shadow-xl border border-slate-800 mb-6">
              <div className="text-[10px] uppercase font-black text-slate-500 px-2 mb-2 tracking-wider">Workspaces</div>
              <div className="flex md:flex-col gap-1 overflow-x-auto pb-1 md:pb-0">
                <button onClick={() => navigate('/intel/global')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/intel') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>1. Intelligence</button>
                <button onClick={() => navigate('/structure/oi')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/structure') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>2. Structure</button>
                <button onClick={() => navigate('/trade/suggester')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/trade') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>3. Trade</button>
                <button onClick={() => navigate('/data/ingest')} className={`px-3 py-2 rounded-xl text-xs font-bold text-left whitespace-nowrap transition ${location.pathname.startsWith('/data') ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-800'}`}>4. Data & Ops</button>
              </div>
            </div>

            {/* TAB NAV (Dynamic based on workspace) */}
            <div className="bg-white p-2 rounded-2xl border border-slate-200 shadow-sm">
              <div className="text-[10px] uppercase font-black text-slate-400 px-2 mb-2 tracking-wider">Views</div>
              <div className="flex md:flex-col gap-1 overflow-x-auto">
                
                {location.pathname.startsWith('/intel') && (
                  <>
                    <button onClick={() => navigate('/intel/global')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/global' ? 'bg-blue-50 text-blue-600' : 'text-slate-600 hover:bg-slate-50'}`}><Globe className="w-4 h-4"/> Global</button>
                    <button onClick={() => navigate('/intel/sector')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/sector' ? 'bg-emerald-50 text-emerald-600' : 'text-slate-600 hover:bg-slate-50'}`}><Newspaper className="w-4 h-4"/> Sector</button>
                    <button onClick={() => navigate('/intel/flows')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/flows' ? 'bg-teal-50 text-teal-600' : 'text-slate-600 hover:bg-slate-50'}`}><Activity className="w-4 h-4"/> Flows</button>
                    <button onClick={() => navigate('/intel/events')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/intel/events' ? 'bg-pink-50 text-pink-600' : 'text-slate-600 hover:bg-slate-50'}`}><Calendar className="w-4 h-4"/> Events</button>
                  </>
                )}

                {location.pathname.startsWith('/structure') && (
                  <>
                    <button onClick={() => navigate('/structure/oi')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/oi' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><BarChart2 className="w-4 h-4"/> OI</button>
                    <button onClick={() => navigate('/structure/vol')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/vol' ? 'bg-amber-50 text-amber-600' : 'text-slate-600 hover:bg-slate-50'}`}><Activity className="w-4 h-4"/> Vol & RND</button>
                    <button onClick={() => navigate('/structure/chart')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/chart' ? 'bg-blue-50 text-blue-600' : 'text-slate-600 hover:bg-slate-50'}`}><TrendingUp className="w-4 h-4"/> Chart</button>
                    <button onClick={() => navigate('/structure/compare')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/structure/compare' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Layers className="w-4 h-4"/> Compare</button>
                  </>
                )}

                {location.pathname.startsWith('/trade') && (
                  <>
                    <button onClick={() => navigate('/trade/suggester')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/suggester' ? 'bg-indigo-600 text-white shadow-md' : 'text-slate-600 hover:bg-slate-50'}`}><Sparkles className="w-4 h-4"/> Suggester</button>
                    <button onClick={() => navigate('/trade/portfolio')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/trade/portfolio' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><Briefcase className="w-4 h-4"/> Portfolio</button>
                  </>
                )}

                {location.pathname.startsWith('/data') && (
                  <>
                    <button onClick={() => navigate('/data/ingest')} className={`px-3 py-2 flex items-center gap-2 rounded-xl text-xs font-bold whitespace-nowrap transition ${location.pathname === '/data/ingest' ? 'bg-indigo-50 text-indigo-600' : 'text-slate-600 hover:bg-slate-50'}`}><DownloadCloud className="w-4 h-4"/> Ingestion</button>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Main Content Area - Renders ALL panels but hides inactive ones to preserve state! */}
          <div className="flex-1 w-full overflow-hidden">
            
            <Routes>
              <Route path="/" element={<Navigate to="/trade/suggester" replace />} />
            </Routes>

            {analytics.success && (
            <div className="transition duration-300">
              
              {/* === INTELLIGENCE === */}
              <div style={{ display: location.pathname === '/intel/global' ? 'block' : 'none' }}>
                <GlobalCuesPanel cues={analytics.globalCues} pctMap={pctMap} onPctChange={(name, val) => setPctMap((prev) => ({ ...prev, [name]: val }))} onResetDefaults={handleResetPct} pipelineRes={pipelineRes} />
              </div>
              
              <div style={{ display: location.pathname === '/intel/sector' ? 'block' : 'none' }}>
                <SectorNewsPanel data={pipelineRes} />
                <div className="mt-6"><SectorEarningsPanel pipelineRes={pipelineRes} /></div>
              </div>
              
              <div style={{ display: location.pathname === '/intel/flows' ? 'block' : 'none' }}>
                <FlowsPanel />
              </div>

              <div style={{ display: location.pathname === '/intel/events' ? 'block' : 'none' }}>
                <EventCalendarPanel conclusion={pipelineRes?.conclusion} />
              </div>

              {/* === STRUCTURE === */}
              <div style={{ display: location.pathname === '/structure/oi' ? 'block' : 'none' }}>
                <OIPositioningPanel rows={analytics.chainRows} spot={analytics.spot} maxPain={analytics.maxPain} pcr={analytics.pcr} reads={analytics.reads} structureContext={analytics.structureContext} breadthInterpretation={pipelineRes?.interpretations?.breadth} />
              </div>

              <div style={{ display: location.pathname === '/structure/vol' ? 'block' : 'none' }}>
                <ComplacencyPanel metrics={analytics.complacencyMetrics} spot={analytics.spot} />
              </div>

              <div style={{ display: location.pathname === '/structure/chart' ? 'block' : 'none' }}>
                <PriceChartPanel />
              </div>

              <div style={{ display: location.pathname === '/structure/compare' ? 'block' : 'none' }}>
                <CaptureComparePanel captures={captures} />
              </div>

              {/* === TRADE === */}
              <div style={{ display: location.pathname === '/trade/suggester' ? 'block' : 'none' }}>
                <StrategySuggesterPanel
                  pipelineRes={pipelineRes} rows={analytics.chainRows} spot={analytics.spot} atmIV={analytics.atmMeta.iv}
                  riskConfig={riskConfig} captureId={selectedCaptureId} onRiskConfigChange={setRiskConfig}
                  mockTrade={mockTrade} onMockTradeChange={setMockTrade} selectedOutlook={traderOutlook}
                  onOutlookChange={setTraderOutlook} optWeights={optWeights} setOptWeights={setOptWeights}
                  optBias={optBias} setOptBias={setOptBias} optMinPop={optMinPop} setOptMinPop={setOptMinPop}
                  optAllowUndefined={optAllowUndefined} setOptAllowUndefined={setOptAllowUndefined}
                  optCostPerLeg={optCostPerLeg} setOptCostPerLeg={setOptCostPerLeg} optWindowPts={optWindowPts}
                  setOptWindowPts={setOptWindowPts} optMaxWing={optMaxWing} setOptMaxWing={setOptMaxWing}
                  optTopN={optTopN} setOptTopN={setOptTopN} optMaxLossBudget={optMaxLossBudget} 
                  setOptMaxLossBudget={setOptMaxLossBudget} optAllowBadRnd={optAllowBadRnd} 
                  setOptAllowBadRnd={setOptAllowBadRnd} onRunPipeline={runQuantPipeline}
                  uploadFile={uploadFile} setUploadFile={setUploadFile} uploadSpot={uploadSpot} setUploadSpot={setUploadSpot}
                  uploadExpiryDate={uploadExpiryDate} setUploadExpiryDate={setUploadExpiryDate} uploadVix={uploadVix} setUploadVix={setUploadVix}
                  onUploadPipeline={onUploadPipeline}
                />
              </div>

              <div style={{ display: location.pathname === '/trade/portfolio' ? 'block' : 'none' }}>
                <PortfolioPanel captures={captures} />
              </div>

              {/* === DATA & OPS === */}
              <div style={{ display: location.pathname === '/data/ingest' ? 'block' : 'none' }}>
                <NSESyncPanel />
                <BreezeSyncPanel 
                  onBreezeDataLoaded={(rows, spot) => {
                    setCsvChainRows(rows);
                    setSpotOverride(spot);
                    alert("ICICI Breeze Option Chain Loaded Successfully!");
                  }} 
                />
              </div>
            </div>
            )}
          </div>
        </div>
        
      </main>

      {/* AI Copilot Modal */}
"""

app_func_marker = "export default function App() {"
app_idx = code.find(app_func_marker)
if app_idx != -1 and "const navigate = useNavigate();" not in code:
    code = code[:app_idx + len(app_func_marker)] + "\n  const navigate = useNavigate();\n  const location = useLocation();\n" + code[app_idx + len(app_func_marker):]

code = code[:start_idx] + new_ui + code[end_idx + len(end_marker):]

with open("src/App.tsx", "w") as f:
    f.write(code)
print("done")
