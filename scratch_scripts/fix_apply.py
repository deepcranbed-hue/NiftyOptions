import re

with open("src/components/StrategySuggesterPanel.tsx", "r") as f:
    content = f.read()

replacement = """
                <button 
                  onClick={() => {
                    if (uploadFile && onUploadPipeline) {
                      onUploadPipeline();
                    } else if (onRunPipeline) {
                      onRunPipeline();
                    }
                  }}
                  className="w-full py-3 mt-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-bold tracking-wide transition-colors shadow-lg"
                >
"""

content = content.replace(
"""                <button 
                  onClick={onRunPipeline}
                  className="w-full py-3 mt-4 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-bold tracking-wide transition-colors shadow-lg"
                >""",
replacement
)

with open("src/components/StrategySuggesterPanel.tsx", "w") as f:
    f.write(content)
