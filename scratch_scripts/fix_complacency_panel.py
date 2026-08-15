import re

with open("src/components/ComplacencyPanel.tsx", "r") as f:
    content = f.read()

replacement = """
          <div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500">
            <span>Put Writing Bursts (&gt;100% OI)</span>
            <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">±250 ATM</span>
          </div>
          {!metrics.has_oi_data ? (
            <div className="text-sm font-semibold text-slate-400 py-2">
              OI-change unavailable
            </div>
          ) : (
            <>
              <div className="text-3xl font-bold text-emerald-600 flex items-baseline gap-2">
                {metrics.bursts} <span className="text-sm font-normal text-slate-400">strikes near spot</span>
              </div>
              <div className="text-xs font-semibold text-slate-700">
                Max Burst Velocity: <span className="text-emerald-700 font-bold">+{metrics.max_burst.toFixed(0)}%</span>
              </div>
            </>
          )}
          <div className="text-[11px] text-slate-400 leading-tight">
"""

content = re.sub(
    r'<div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500">\n            <span>Put Writing Bursts \(&gt;100% OI\)</span>\n            <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800">±250 ATM</span>\n          </div>\n          <div className="text-3xl font-bold text-emerald-600 flex items-baseline gap-2">\n            \{metrics.bursts\} <span className="text-sm font-normal text-slate-400">strikes near spot</span>\n          </div>\n          <div className="text-xs font-semibold text-slate-700">\n            Max Burst Velocity: <span className="text-emerald-700 font-bold">\+\{metrics.max_burst.toFixed\(0\)\}%</span>\n          </div>\n          <div className="text-\[11px\] text-slate-400 leading-tight">',
    replacement.strip() + '\n',
    content
)

with open("src/components/ComplacencyPanel.tsx", "w") as f:
    f.write(content)
