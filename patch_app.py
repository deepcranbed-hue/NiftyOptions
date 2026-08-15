content = open("src/App.tsx").read()

# 1. Remove import
content = content.replace("import { ChainEditorDrawer } from './components/ChainEditorDrawer';\n", "")

# 2. Remove state
content = content.replace("  const [isChainDrawerOpen, setIsChainDrawerOpen] = useState(false);\n", "")

# 3. Remove Button 1
b1_target = """            <button
              onClick={() => setIsChainDrawerOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition border border-slate-700/80 cursor-pointer"
            >
              <Database className="w-3.5 h-3.5 text-blue-400" />
              <span className="hidden md:inline">Edit Chain Data</span>
              {analytics.success && <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block animate-pulse"></span>}
            </button>"""
content = content.replace(b1_target, "")

# 4. Remove Button 2
b2_target = """              <button
                onClick={() => setIsChainDrawerOpen(true)}
                className="px-4 py-2 rounded-xl bg-rose-600 text-white font-bold text-xs hover:bg-rose-700 transition inline-block"
              >
                Open Option Chain Editor
              </button>"""
content = content.replace(b2_target, "")

# 5. Remove Component
comp_target = """      {/* Option Chain Editor Drawer */}
      <ChainEditorDrawer
        isOpen={isChainDrawerOpen}
        onClose={() => setIsChainDrawerOpen(false)}
        rawChain={rawChain}
        onSaveChain={(newChain, newSpot, newDte) => {
          setRawChain(newChain);
          setSpotOverride(newSpot);
          setDteOverride(newDte);
        }}
      />"""
content = content.replace(comp_target, "")

with open("src/App.tsx", "w") as f:
    f.write(content)
