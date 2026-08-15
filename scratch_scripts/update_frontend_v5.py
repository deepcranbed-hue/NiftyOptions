import re

with open("src/components/EventCalendarPanel.tsx", "r") as f:
    content = f.read()

# Add props interface
if "interface EventCalendarPanelProps" not in content:
    content = content.replace(
        "export function EventCalendarPanel() {",
        "interface EventCalendarPanelProps {\n  conclusion?: any;\n}\n\nexport function EventCalendarPanel({ conclusion }: EventCalendarPanelProps) {"
    )

# Add footer UI
footer_ui = """
      {/* Conclusion Footer */}
      {conclusion && (
        <div className="p-4 border-t border-gray-800 bg-gray-800/20">
          <h3 className={`text-sm font-bold uppercase tracking-wider mb-2 ${
            conclusion.posture === 'defensive' ? 'text-red-400' : 
            conclusion.posture === 'cautious' ? 'text-yellow-400' : 'text-emerald-400'
          }`}>
            {conclusion.headline}
          </h3>
          <ul className="space-y-2 mb-3">
            {conclusion.points.map((pt: string, i: number) => (
              <li key={i} className="text-xs text-gray-300 flex items-start gap-2">
                <span className="text-gray-500 mt-0.5">•</span>
                <span>{pt}</span>
              </li>
            ))}
          </ul>
          <p className="text-[10px] text-gray-500 italic border-t border-gray-800/50 pt-2">
            {conclusion.disclaimer}
          </p>
        </div>
      )}
"""

if "Conclusion Footer" not in content:
    content = content.replace(
        "    </div>\n  );\n}",
        footer_ui + "\n    </div>\n  );\n}"
    )

with open("src/components/EventCalendarPanel.tsx", "w") as f:
    f.write(content)

with open("src/App.tsx", "r") as f:
    app_content = f.read()

if "conclusion={pipelineRes?.conclusion}" not in app_content:
    app_content = app_content.replace(
        "<EventCalendarPanel />",
        "<EventCalendarPanel conclusion={pipelineRes?.conclusion} />"
    )

with open("src/App.tsx", "w") as f:
    f.write(app_content)

