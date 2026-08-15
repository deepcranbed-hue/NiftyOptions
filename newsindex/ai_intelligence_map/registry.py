"""
registry.py  —  The curated backbone of the AI Industry Intelligence Map.

This is the file you EDIT. It holds the qualitative intelligence that no API
gives you: each company's role, moat, biggest risk, who it depends on, its key
KPIs, the value-chain layer + investment "phase" it sits in, a one-line
bull/bear, and the authoritative source URLs used to keep it current.

The engine (ai_map.py) overlays LIVE quantitative data (price, market cap, P/E,
revenue growth, margins, YTD move) onto every row that has a public `ticker`,
and tags fresh news headlines to each company. Private companies (no ticker)
carry a curated last-known valuation + the source it came from.

Schema of each company dict
---------------------------
name         : display name
ticker       : Yahoo Finance symbol, or None if private/unlisted
layer        : one of LAYERS keys (see below)
sublayer     : finer bucket within the layer (free text)
phase        : investment phase 1-4 (see PHASES) — when this name tends to lead
role         : what they actually sell
moat         : durable competitive advantage
risk         : biggest structural risk
depends_on   : upstream dependencies (who they need)
customers    : key customers / demand drivers
kpis         : the numbers to watch each quarter (list)
bull         : one-line bull case
bear         : one-line bear case
priv_val     : curated valuation string for PRIVATE names (else None)
priv_val_date: as-of date for priv_val
sources      : authoritative URLs to consolidate / re-read when updating

Edit freely. Add companies, add sources, refine the moats. The dashboard and
reports rebuild from whatever is in this list.
"""

# --- Value-chain layers (top-down) -------------------------------------------
LAYERS = {
    "L1_SEMI":    "Layer 1 — Semiconductors & Silicon",
    "L2_MODELS":  "Layer 2 — AI Model / Frontier Labs",
    "L3_CLOUD":   "Layer 3 — Cloud & Compute Providers",
    "L4_NET":     "Layer 4 — AI Networking & Interconnect",
    "L5_POWER":   "Layer 5 — Power, Cooling & Energy",
    "L6_APPS":    "Layer 6 — Applications & Enterprise Software",
}

# --- The four economic phases (your mental model) ----------------------------
PHASES = {
    1: "Phase 1 (build GPUs)",
    2: "Phase 2 (build data centres)",
    3: "Phase 3 (deploy models)",
    4: "Phase 4 (monetise AI)",
}

# --- Shared / sector-level source library ------------------------------------
# Consolidated in the report's "Sources" section; re-read these when updating.
SECTOR_SOURCES = {
    "Custom AI ASICs (Broadcom/TPU/MTIA/Trainium/Maia)":
        "https://www.tomshardware.com/tech-industry/semiconductors/custom-ai-asics-examined-from-broadcom-to-mtia",
    "AI-trade / chip-stock market structure":
        "https://www.investopedia.com/",
    "Semiconductor deep analysis (SemiAnalysis)":
        "https://www.semianalysis.com/",
    "HBM & memory cycle":
        "https://www.tomshardware.com/pc-components/dram",
}

COMPANIES = [
    # =====================================================================
    # LAYER 1 — SEMICONDUCTORS & SILICON
    # =====================================================================
    {
        "name": "NVIDIA", "ticker": "NVDA", "layer": "L1_SEMI",
        "sublayer": "AI accelerators (GPU)", "phase": 1,
        "role": "Data-centre GPUs + the CUDA software stack + NVLink/networking.",
        "moat": "CUDA developer lock-in, full-stack (GPU+network+software), pace of roadmap.",
        "risk": "Every hyperscaler is building its own inference ASIC; margin normalisation.",
        "depends_on": "TSMC (fab), SK Hynix/Micron/Samsung (HBM), CoWoS packaging.",
        "customers": "Microsoft, Meta, Amazon, Google, Oracle, CoreWeave, xAI, enterprises.",
        "kpis": ["Data-centre revenue", "gross margin", "Blackwell/Rubin ramp", "HBM supply", "backlog / purchase commitments"],
        "bull": "Owns the training ecosystem; CUDA moat + fastest roadmap keep it the default.",
        "bear": "Custom ASICs erode inference share; hyperscaler capex digestion caps growth.",
        "priv_val": None, "priv_val_date": None,
        "sources": [
            "https://investor.nvidia.com/",
            "https://www.tomshardware.com/tech-industry/semiconductors/custom-ai-asics-examined-from-broadcom-to-mtia",
        ],
    },
    {
        "name": "AMD", "ticker": "AMD", "layer": "L1_SEMI",
        "sublayer": "AI accelerators (GPU) + CPU", "phase": 1,
        "role": "Instinct MI-series GPUs, EPYC server CPUs, ROCm software.",
        "moat": "Lower cost/perf, strong EPYC franchise, credible #2 the market wants alive.",
        "risk": "ROCm ecosystem still trails CUDA; execution vs NVIDIA's cadence.",
        "depends_on": "TSMC, HBM suppliers, packaging.",
        "customers": "Microsoft, Meta, Oracle, cloud + HPC labs.",
        "kpis": ["MI-series data-centre GPU revenue", "ROCm adoption", "EPYC server share", "gross margin"],
        "bull": "Only credible alternative accelerator; MI ramp + EPYC share gains.",
        "bear": "Second ecosystem; hard to dislodge CUDA; margin gap to NVIDIA.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://ir.amd.com/"],
    },
    {
        "name": "Broadcom", "ticker": "AVGO", "layer": "L1_SEMI",
        "sublayer": "Custom AI ASICs + networking silicon", "phase": 2,
        "role": "Designs custom AI chips (XPUs) for hyperscalers + Ethernet/switch silicon.",
        "moat": "Wins regardless of which hyperscaler wins; deep custom-silicon + IP relationships.",
        "risk": "Customer concentration (few very large buyers); program timing lumpiness.",
        "depends_on": "TSMC, advanced packaging, HBM.",
        "customers": "Google (TPU), Meta (MTIA), and other hyperscaler custom programs.",
        "kpis": ["AI semiconductor revenue", "# of custom-silicon customers", "networking mix", "bookings"],
        "bull": "The 'arms dealer' of custom AI silicon — earns on every hyperscaler's chip.",
        "bear": "Concentrated in a handful of customers; any in-sourcing hurts.",
        "priv_val": None, "priv_val_date": None,
        "sources": [
            "https://investors.broadcom.com/",
            "https://www.tomshardware.com/tech-industry/semiconductors/custom-ai-asics-examined-from-broadcom-to-mtia",
        ],
    },
    {
        "name": "Marvell", "ticker": "MRVL", "layer": "L1_SEMI",
        "sublayer": "Custom AI silicon + optical interconnect", "phase": 2,
        "role": "Custom compute silicon, optical DSPs, interconnect for AI clusters.",
        "moat": "AI interconnect + custom-silicon design wins; optical leadership.",
        "risk": "Competes head-on with Broadcom; program concentration.",
        "depends_on": "TSMC, optical component supply.",
        "customers": "Hyperscalers, networking OEMs.",
        "kpis": ["AI/data-centre revenue", "custom-silicon design wins", "optical DSP share"],
        "bull": "Second big beneficiary of custom silicon + optical interconnect boom.",
        "bear": "Smaller than Broadcom; direct competition compresses economics.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.marvell.com/company/investor-relations.html"],
    },
    {
        "name": "TSMC", "ticker": "TSM", "layer": "L1_SEMI",
        "sublayer": "Foundry (manufacturing)", "phase": 2,
        "role": "Manufactures the advanced chips almost every AI designer relies on.",
        "moat": "Near-monopoly in leading-edge nodes + CoWoS advanced packaging.",
        "risk": "Taiwan geopolitical exposure; capex intensity; cyclicality.",
        "depends_on": "ASML (litho), materials, power; concentrated fab geography.",
        "customers": "NVIDIA, Apple, AMD, Broadcom, Qualcomm, Marvell — the whole industry.",
        "kpis": ["Advanced-node revenue mix", "CoWoS capacity", "utilisation", "capex", "HPC segment growth"],
        "bull": "The AI 'toll booth' — everyone pays TSMC; packaging is the new bottleneck.",
        "bear": "Single-region concentration; a Taiwan shock is systemic.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investor.tsmc.com/english"],
    },
    {
        "name": "Micron", "ticker": "MU", "layer": "L1_SEMI",
        "sublayer": "Memory / HBM", "phase": 2,
        "role": "DRAM incl. HBM (high-bandwidth memory) that every AI GPU needs.",
        "moat": "One of three HBM makers; capacity is sold out ahead.",
        "risk": "Memory pricing is cyclical; supply gluts crush margins.",
        "depends_on": "Fab equipment, process yields.",
        "customers": "NVIDIA, AMD, custom-ASIC makers.",
        "kpis": ["HBM revenue + bit share", "DRAM ASP trend", "gross margin", "HBM sold-out status"],
        "bull": "HBM is a hard AI bottleneck; pricing power while capacity is tight.",
        "bear": "Commodity-memory cyclicality can reverse fast.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investors.micron.com/"],
    },
    {
        "name": "SK Hynix", "ticker": "000660.KS", "layer": "L1_SEMI",
        "sublayer": "Memory / HBM", "phase": 2,
        "role": "Leading HBM supplier (lead customer historically NVIDIA).",
        "moat": "HBM technology lead + first-mover volume with the top GPU buyer.",
        "risk": "Supply constraints; memory cycle; competitive catch-up.",
        "depends_on": "Fab capacity, packaging, yields.",
        "customers": "NVIDIA and other accelerator makers.",
        "kpis": ["HBM market share", "HBM3E/next-gen qualification", "DRAM pricing"],
        "bull": "HBM leadership with the dominant GPU vendor as anchor customer.",
        "bear": "Memory-cycle exposure; rivals closing the HBM gap.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.skhynix.com/ir/UI-FR-IR01/"],
    },
    {
        "name": "Qualcomm", "ticker": "QCOM", "layer": "L1_SEMI",
        "sublayer": "Edge / on-device AI", "phase": 4,
        "role": "Mobile + edge SoCs running on-device AI; auto & IoT expansion.",
        "moat": "Modem/RF + on-device AI leadership across the phone base.",
        "risk": "Limited data-centre AI exposure; Apple modem in-sourcing; handset cyclicality.",
        "depends_on": "TSMC/Samsung foundry, OEM design wins.",
        "customers": "Android OEMs, auto, IoT.",
        "kpis": ["On-device AI attach", "auto design-win pipeline", "handset volumes"],
        "bull": "Owns the on-device inference layer as AI moves to the edge.",
        "bear": "Under-exposed to the data-centre gold rush; customer in-sourcing.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investor.qualcomm.com/"],
    },
    {
        "name": "Intel", "ticker": "INTC", "layer": "L1_SEMI",
        "sublayer": "CPU + accelerators + foundry", "phase": 2,
        "role": "Server/PC CPUs, Gaudi accelerators, and a foundry ambition.",
        "moat": "Manufacturing footprint + x86 base; potential foundry #2 to TSMC.",
        "risk": "Execution risk on process + foundry; accelerator share vs NVIDIA/AMD.",
        "depends_on": "Own fabs (process roadmap), external EUV tools.",
        "customers": "PC/server OEMs, cloud, (foundry) external chip designers.",
        "kpis": ["Foundry external revenue", "process-node milestones", "Gaudi traction", "gross margin"],
        "bull": "Turnaround + Western foundry optionality if the process lands.",
        "bear": "Chronic execution risk; behind in AI accelerators.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.intc.com/"],
    },
    {
        "name": "Arm Holdings", "ticker": "ARM", "layer": "L1_SEMI",
        "sublayer": "CPU IP / architecture", "phase": 2,
        "role": "Licenses the CPU architecture in nearly every phone + growing server share.",
        "moat": "Ubiquitous, power-efficient ISA; royalty on almost every SoC.",
        "risk": "Royalty-based growth is slow; RISC-V long-tail threat; customer in-sourcing.",
        "depends_on": "Ecosystem adoption; licensees shipping volume.",
        "customers": "Apple, Qualcomm, NVIDIA (Grace), hyperscaler custom CPUs.",
        "kpis": ["Royalty revenue per chip", "v9 adoption", "data-centre CPU design wins"],
        "bull": "Taxes the entire compute base; rising server + AI-CPU attach.",
        "bear": "Royalty model grows slowly; valuation rich; RISC-V erosion risk.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investors.arm.com/"],
    },
    {
        "name": "ASML", "ticker": "ASML", "layer": "L1_SEMI",
        "sublayer": "Lithography equipment", "phase": 2,
        "role": "Sole supplier of EUV lithography — the machine that makes advanced chips.",
        "moat": "Monopoly on EUV; no substitute for leading-edge manufacturing.",
        "risk": "Export controls; capex cyclicality; lumpy orders.",
        "depends_on": "Deep supplier base (Zeiss optics etc.).",
        "customers": "TSMC, Samsung, Intel.",
        "kpis": ["EUV/High-NA bookings", "backlog", "China exposure vs controls"],
        "bull": "Bottleneck-of-the-bottleneck — no advanced AI chip without ASML.",
        "bear": "Export-control drag; order timing volatility.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.asml.com/en/investors"],
    },

    # =====================================================================
    # LAYER 2 — AI MODEL / FRONTIER LABS
    # =====================================================================
    {
        "name": "OpenAI", "ticker": None, "layer": "L2_MODELS",
        "sublayer": "Frontier lab (private)", "phase": 3,
        "role": "ChatGPT, GPT API, enterprise, agents, Codex — largest AI app ecosystem.",
        "moat": "Consumer brand + largest distribution + product velocity.",
        "risk": "Enormous inference cost; open-source pressure; Microsoft dependence.",
        "depends_on": "Microsoft/Azure + additional compute (Oracle, custom silicon), NVIDIA GPUs.",
        "customers": "Consumers, developers, enterprises.",
        "kpis": ["Weekly active users", "ARR / API revenue run-rate", "compute cost/token", "enterprise seats"],
        "bull": "Default consumer AI; broadest ecosystem across chat/API/agents.",
        "bear": "Cash-burning inference economics; commoditisation from open models.",
        "priv_val": "≈ private; last major round valued it in the hundreds of billions (verify latest)",
        "priv_val_date": "verify — changes with each round",
        "sources": ["https://openai.com/news/", "https://openai.com/"],
    },
    {
        "name": "Anthropic", "ticker": None, "layer": "L2_MODELS",
        "sublayer": "Frontier lab (private)", "phase": 3,
        "role": "Claude models — enterprise, coding, long-context reasoning, safety focus.",
        "moat": "Enterprise + coding strength; safety reputation; strong API/agents traction.",
        "risk": "Smaller consumer presence than ChatGPT; compute cost; frontier race.",
        "depends_on": "Amazon (AWS/Trainium) + Google Cloud compute, NVIDIA GPUs.",
        "customers": "Enterprises, developers, coding platforms.",
        "kpis": ["Enterprise + API ARR", "coding-market share", "model win-rate", "compute partners"],
        "bull": "Enterprise/coding wedge + safety brand; fastest-growing API revenue.",
        "bear": "Thinner consumer funnel; must keep pace at the frontier on huge compute.",
        "priv_val": "≈ private; last major round valued it in the tens-to-hundreds of billions (verify latest)",
        "priv_val_date": "verify — changes with each round",
        "sources": ["https://www.anthropic.com/news", "https://www.anthropic.com/"],
    },
    {
        "name": "Google DeepMind (Alphabet)", "ticker": "GOOGL", "layer": "L2_MODELS",
        "sublayer": "Frontier lab (public via Alphabet)", "phase": 3,
        "role": "Gemini models + world-class research + owns TPUs and distribution.",
        "moat": "Full stack: research + TPU silicon + Search/YouTube/Android/Cloud distribution.",
        "risk": "AI can cannibalise Search ad economics; org execution/monetisation.",
        "depends_on": "Own TPUs (via Broadcom) + NVIDIA; TSMC.",
        "customers": "Consumers (Search/Workspace), GCP enterprises, developers.",
        "kpis": ["Gemini usage", "Cloud AI revenue", "Search monetisation trend", "TPU deployment"],
        "bull": "Only lab that owns model + chip + distribution end-to-end.",
        "bear": "Search is the crown jewel AI could disrupt; monetisation tension.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://abc.xyz/investor/", "https://deepmind.google/"],
    },
    {
        "name": "Meta AI (Llama)", "ticker": "META", "layer": "L2_MODELS",
        "sublayer": "Open-weight models (public)", "phase": 3,
        "role": "Llama open models + AI woven into a multi-billion-user social base.",
        "moat": "Distribution to billions + open-weight ecosystem leadership + huge capex.",
        "risk": "Monetisation of AI still evolving; capex vs return scrutiny.",
        "depends_on": "NVIDIA + own MTIA silicon (via Broadcom), TSMC.",
        "customers": "Its own apps (ads engine), open-source developer ecosystem.",
        "kpis": ["AI capex", "ad-engine lift from AI", "Llama adoption", "MTIA deployment"],
        "bull": "AI improves the ad machine now; open models seed the ecosystem.",
        "bear": "Direct AI monetisation unclear; capex ahead of proven return.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investor.atmeta.com/", "https://ai.meta.com/"],
    },
    {
        "name": "xAI", "ticker": None, "layer": "L2_MODELS",
        "sublayer": "Frontier lab (private)", "phase": 3,
        "role": "Grok models, deeply integrated with X; fast iteration + big GPU build-out.",
        "moat": "Speed of execution + X distribution + aggressive compute build.",
        "risk": "Smaller enterprise footprint; funding intensity; ecosystem depth.",
        "depends_on": "NVIDIA GPUs, own data-centre build (Colossus), power.",
        "customers": "X users, developers, emerging enterprise.",
        "kpis": ["Grok usage on X", "GPU cluster size", "API traction", "funding runway"],
        "bull": "Fastest-moving new entrant with built-in distribution via X.",
        "bear": "Thin enterprise presence; capital-intensive catch-up.",
        "priv_val": "≈ private; valued in the tens of billions at recent raises (verify latest)",
        "priv_val_date": "verify — changes with each round",
        "sources": ["https://x.ai/"],
    },
    {
        "name": "Mistral AI", "ticker": None, "layer": "L2_MODELS",
        "sublayer": "Efficient open models (private, EU)", "phase": 3,
        "role": "Efficient open-weight models; European sovereignty angle.",
        "moat": "Capital-efficient models + EU/sovereign positioning.",
        "risk": "Scale disadvantage vs US hyperscaler-backed labs; compute access.",
        "depends_on": "NVIDIA GPUs, cloud partners.",
        "customers": "European enterprises, developers, sovereign deployments.",
        "kpis": ["Model download/adoption", "enterprise deals", "EU/sovereign contracts"],
        "bull": "Efficiency + European sovereignty demand carve a defensible niche.",
        "bear": "Out-scaled and out-spent by the frontier leaders.",
        "priv_val": "≈ private; valued in the low tens of billions (verify latest)",
        "priv_val_date": "verify — changes with each round",
        "sources": ["https://mistral.ai/news/"],
    },
    {
        "name": "Cohere", "ticker": None, "layer": "L2_MODELS",
        "sublayer": "Enterprise models (private)", "phase": 3,
        "role": "Enterprise-focused LLMs + retrieval; data-privacy positioning.",
        "moat": "Enterprise/retrieval focus + neutral (non-hyperscaler-owned) stance.",
        "risk": "Limited consumer reach; crowded enterprise field.",
        "depends_on": "Cloud/GPU partners.",
        "customers": "Enterprises needing private deployments.",
        "kpis": ["Enterprise ARR", "retrieval/RAG deals", "on-prem/VPC adoption"],
        "bull": "Neutral enterprise vendor for firms wary of the big labs.",
        "bear": "Small scale; differentiation under pressure.",
        "priv_val": "≈ private; valued in the low single-digit billions (verify latest)",
        "priv_val_date": "verify — changes with each round",
        "sources": ["https://cohere.com/"],
    },

    # =====================================================================
    # LAYER 3 — CLOUD & COMPUTE PROVIDERS
    # =====================================================================
    {
        "name": "Microsoft", "ticker": "MSFT", "layer": "L3_CLOUD",
        "sublayer": "Hyperscaler (Azure) + OpenAI + Copilot", "phase": 4,
        "role": "Azure cloud, OpenAI partnership, Copilot across Office/GitHub, Maia silicon.",
        "moat": "Enterprise distribution + OpenAI access + full-stack Copilot monetisation.",
        "risk": "Capex intensity; OpenAI relationship dynamics; Copilot ROI proof.",
        "depends_on": "NVIDIA + own Maia (via Broadcom), OpenAI models, power.",
        "customers": "Enterprises, developers, consumers (Office/Windows).",
        "kpis": ["Azure AI revenue", "Copilot seats/attach", "capex", "AI backlog"],
        "bull": "Best-placed to monetise AI via enterprise distribution + Copilot.",
        "bear": "Heavy capex; must prove Copilot ROI; OpenAI dependency.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.microsoft.com/en-us/investor"],
    },
    {
        "name": "Amazon", "ticker": "AMZN", "layer": "L3_CLOUD",
        "sublayer": "Hyperscaler (AWS) + Trainium/Inferentia", "phase": 4,
        "role": "AWS cloud, Bedrock model marketplace, Trainium/Inferentia custom silicon.",
        "moat": "Largest cloud install base + custom silicon lowering AI cost + Anthropic stake.",
        "risk": "Capex; must show AI reaccelerates AWS; silicon adoption vs NVIDIA.",
        "depends_on": "NVIDIA + own Trainium (Annapurna), TSMC, power.",
        "customers": "AWS enterprise base, startups, Anthropic.",
        "kpis": ["AWS growth + AI mix", "Trainium adoption", "Bedrock usage", "capex"],
        "bull": "Owns the biggest cloud + cheapest custom AI silicon path.",
        "bear": "Capex-heavy; NVIDIA still default; AI-revenue proof pending.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://ir.aboutamazon.com/"],
    },
    {
        "name": "Oracle", "ticker": "ORCL", "layer": "L3_CLOUD",
        "sublayer": "AI cloud (OCI)", "phase": 2,
        "role": "OCI GPU cloud + large AI-infrastructure contracts and backlog.",
        "moat": "Aggressive GPU-cloud capacity + database install base + big signed backlog.",
        "risk": "Capex + debt to fund build-out; customer concentration in backlog.",
        "depends_on": "NVIDIA GPUs, power, data-centre build.",
        "customers": "AI labs, enterprises needing GPU capacity.",
        "kpis": ["RPO / signed backlog", "OCI revenue growth", "capex", "GPU capacity online"],
        "bull": "Reinvented as a GPU-cloud with a massive contracted backlog.",
        "bear": "Funding the build with debt; backlog concentration risk.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investor.oracle.com/"],
    },
    {
        "name": "CoreWeave", "ticker": "CRWV", "layer": "L3_CLOUD",
        "sublayer": "Specialised GPU cloud", "phase": 2,
        "role": "Pure-play GPU cloud renting NVIDIA capacity to AI labs.",
        "moat": "Speed to deploy latest NVIDIA systems; deep NVIDIA relationship.",
        "risk": "High leverage; customer concentration; GPU depreciation + rate sensitivity.",
        "depends_on": "NVIDIA allocation, financing, power.",
        "customers": "AI labs and hyperscalers needing burst GPU capacity.",
        "kpis": ["Contracted backlog", "revenue growth", "GPU utilisation", "debt/interest coverage"],
        "bull": "Cleanest public proxy for GPU-cloud demand.",
        "bear": "Leverage + concentration + depreciation make it high-beta.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investors.coreweave.com/"],
    },

    # =====================================================================
    # LAYER 4 — AI NETWORKING & INTERCONNECT
    # =====================================================================
    {
        "name": "Arista Networks", "ticker": "ANET", "layer": "L4_NET",
        "sublayer": "Data-centre Ethernet switching", "phase": 2,
        "role": "High-speed Ethernet switches that stitch GPU clusters together.",
        "moat": "Leadership in cloud/AI back-end Ethernet + EOS software.",
        "risk": "Hyperscaler in-sourcing / white-box; customer concentration.",
        "depends_on": "Merchant silicon (Broadcom), hyperscaler capex.",
        "customers": "Hyperscalers, large enterprises.",
        "kpis": ["AI back-end network revenue", "400/800G ramp", "cloud-titan mix"],
        "bull": "GPUs are worthless unconnected — Arista sells the fabric.",
        "bear": "Concentrated buyers who could move to white-box.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investors.arista.com/"],
    },
    # (Broadcom & Marvell also anchor this layer — see Layer 1.)

    # =====================================================================
    # LAYER 5 — POWER, COOLING & ENERGY
    # =====================================================================
    {
        "name": "Vertiv", "ticker": "VRT", "layer": "L5_POWER",
        "sublayer": "Data-centre power & cooling", "phase": 2,
        "role": "Power management + liquid/thermal cooling for AI data centres.",
        "moat": "Scale + product breadth as liquid cooling becomes mandatory.",
        "risk": "Cyclical capex; competition; supply-chain constraints.",
        "depends_on": "Data-centre build cycle, components.",
        "customers": "Hyperscalers, colos, enterprises.",
        "kpis": ["Orders/book-to-bill", "liquid-cooling mix", "backlog", "margin"],
        "bull": "Direct pick-and-shovel play on AI's power + heat problem.",
        "bear": "Capex-cycle sensitivity; competitive thermal market.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investors.vertiv.com/"],
    },
    {
        "name": "Eaton", "ticker": "ETN", "layer": "L5_POWER",
        "sublayer": "Electrical power management", "phase": 2,
        "role": "Electrical gear (switchgear, UPS, distribution) for data centres + grid.",
        "moat": "Broad electrification franchise riding data-centre + grid capex.",
        "risk": "Macro/industrial cyclicality; project timing.",
        "depends_on": "Industrial supply chain, utility capex.",
        "customers": "Data centres, utilities, industrials.",
        "kpis": ["Data-centre orders", "backlog", "electrical segment growth"],
        "bull": "Electrification + AI data-centre power demand as a durable tailwind.",
        "bear": "Industrial-cycle exposure.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.eaton.com/us/en-us/company/investor-relations.html"],
    },
    {
        "name": "Schneider Electric", "ticker": "SU.PA", "layer": "L5_POWER",
        "sublayer": "Energy management + data-centre infrastructure", "phase": 2,
        "role": "Power distribution, cooling, and data-centre infrastructure at scale.",
        "moat": "Global electrification + data-centre franchise; software (EcoStruxure).",
        "risk": "Cyclicality; FX; project execution.",
        "depends_on": "Data-centre + grid capex cycle.",
        "customers": "Data centres, industry, utilities.",
        "kpis": ["Data-centre revenue", "orders", "backlog", "cooling mix"],
        "bull": "One-stop electrification + cooling supplier for AI data centres.",
        "bear": "Broad industrial exposure dilutes the pure-AI signal.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.se.com/ww/en/about-us/investor-relations/"],
    },
    {
        "name": "GE Vernova", "ticker": "GEV", "layer": "L5_POWER",
        "sublayer": "Power generation & grid", "phase": 2,
        "role": "Gas turbines, grid equipment, and generation to feed data-centre load.",
        "moat": "Generation + grid installed base as electricity becomes the AI bottleneck.",
        "risk": "Long project cycles; policy/energy-transition swings.",
        "depends_on": "Utility + IPP capex, supply chain.",
        "customers": "Utilities, IPPs, grid operators.",
        "kpis": ["Power/grid orders", "backlog", "turbine demand from data centres"],
        "bull": "AI's electricity demand needs new generation + grid — GEV supplies both.",
        "bear": "Slow, lumpy, policy-exposed project business.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.gevernova.com/investors"],
    },

    # =====================================================================
    # LAYER 6 — APPLICATIONS & ENTERPRISE SOFTWARE
    # =====================================================================
    {
        "name": "Apple", "ticker": "AAPL", "layer": "L6_APPS",
        "sublayer": "Consumer devices + on-device AI", "phase": 4,
        "role": "Turns AI into a device feature (Apple Intelligence) across a huge install base.",
        "moat": "~2bn-device base + on-device privacy + ecosystem lock-in.",
        "risk": "Perceived AI laggard; partner-model dependence; regulatory.",
        "depends_on": "TSMC silicon, possibly external frontier models.",
        "customers": "Consumers (the device base).",
        "kpis": ["iPhone upgrade cycle on AI features", "Services growth", "on-device AI adoption"],
        "bull": "AI as a feature that sells devices + Services to billions — monetise last, monetise big.",
        "bear": "Behind on frontier AI; leans on partners for models.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investor.apple.com/"],
    },
    {
        "name": "Adobe", "ticker": "ADBE", "layer": "L6_APPS",
        "sublayer": "Creative software + GenAI", "phase": 4,
        "role": "Firefly generative AI baked into Creative Cloud + Document Cloud.",
        "moat": "Creative-workflow lock-in + commercially-safe training data.",
        "risk": "GenAI could lower the barrier to creation (dis-intermediation).",
        "depends_on": "Cloud compute, model partners + own models.",
        "customers": "Creatives, marketers, enterprises.",
        "kpis": ["AI-influenced ARR", "Firefly usage", "net retention", "seat growth"],
        "bull": "Embeds AI into sticky workflows with safe IP — upsell, not disruption.",
        "bear": "Cheap GenAI tools chip at the low end.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.adobe.com/investor-relations.html"],
    },
    {
        "name": "Salesforce", "ticker": "CRM", "layer": "L6_APPS",
        "sublayer": "Enterprise SaaS + AI agents", "phase": 4,
        "role": "CRM + Agentforce AI agents across sales/service.",
        "moat": "Enterprise data + workflow ownership; distribution to install base.",
        "risk": "Agent ROI proof; seat-based model vs consumption; competition.",
        "depends_on": "Cloud compute, model partners.",
        "customers": "Enterprise sales/service orgs.",
        "kpis": ["Agentforce adoption/consumption", "AI ARR", "net retention"],
        "bull": "Owns enterprise workflow + data to deploy agents at scale.",
        "bear": "Must prove agent ROI; pricing-model transition risk.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investor.salesforce.com/"],
    },
    {
        "name": "ServiceNow", "ticker": "NOW", "layer": "L6_APPS",
        "sublayer": "Enterprise workflow + AI", "phase": 4,
        "role": "Workflow platform embedding AI (Now Assist) across IT/HR/ops.",
        "moat": "System-of-action for enterprise workflows; high retention.",
        "risk": "Premium valuation; AI-ROI proof; macro IT budgets.",
        "depends_on": "Cloud compute, model partners.",
        "customers": "Large enterprises, governments.",
        "kpis": ["Now Assist ACV", "AI-plus SKU attach", "net retention", "cRPO"],
        "bull": "Central nervous system for enterprise workflow — prime agent surface.",
        "bear": "High expectations already priced in.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.servicenow.com/company/investor-relations.html"],
    },
    {
        "name": "Intuit", "ticker": "INTU", "layer": "L6_APPS",
        "sublayer": "Fintech SaaS + AI", "phase": 4,
        "role": "TurboTax/QuickBooks with AI (Intuit Assist) for SMBs + consumers.",
        "moat": "SMB + tax data moat; distribution; workflow lock-in.",
        "risk": "AI-ROI proof; competition; regulatory (tax).",
        "depends_on": "Cloud compute, model partners.",
        "customers": "SMBs, consumers, accountants.",
        "kpis": ["AI feature attach", "SMB online ARR", "net retention"],
        "bull": "Rich proprietary financial data to power vertical AI.",
        "bear": "Must convert AI features into pricing power.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investors.intuit.com/"],
    },
    {
        "name": "SAP", "ticker": "SAP", "layer": "L6_APPS",
        "sublayer": "Enterprise ERP + AI", "phase": 4,
        "role": "ERP backbone embedding AI (Joule) across enterprise operations.",
        "moat": "Mission-critical ERP lock-in + cloud migration (RISE).",
        "risk": "Slow-moving migrations; AI-ROI proof; competition.",
        "depends_on": "Cloud compute, model partners.",
        "customers": "Large global enterprises.",
        "kpis": ["Cloud backlog", "Joule/AI attach", "RISE migrations"],
        "bull": "Runs the world's transactions — a huge surface for embedded AI.",
        "bear": "Migration-paced; AI monetisation gradual.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://www.sap.com/investors.html"],
    },
    {
        "name": "Autodesk", "ticker": "ADSK", "layer": "L6_APPS",
        "sublayer": "Design/engineering software + AI", "phase": 4,
        "role": "CAD/AEC/manufacturing design software with generative-design AI.",
        "moat": "Professional design-workflow lock-in across AEC + manufacturing.",
        "risk": "Cyclical (construction/manufacturing); AI-ROI proof.",
        "depends_on": "Cloud compute, model partners.",
        "customers": "Architecture, engineering, construction, manufacturing.",
        "kpis": ["AI-feature adoption", "net retention", "cloud/AEC growth"],
        "bull": "Generative design deepens an already-sticky professional toolset.",
        "bear": "End-market cyclicality; slower AI uptake.",
        "priv_val": None, "priv_val_date": None,
        "sources": ["https://investors.autodesk.com/"],
    },
]


def companies_in_layer(layer_key):
    return [c for c in COMPANIES if c["layer"] == layer_key]


def all_tickers():
    return [c["ticker"] for c in COMPANIES if c["ticker"]]


if __name__ == "__main__":
    # Quick sanity print
    print(f"{len(COMPANIES)} companies across {len(LAYERS)} layers")
    for k, label in LAYERS.items():
        n = len(companies_in_layer(k))
        print(f"  {label}: {n}")
    print(f"Public tickers: {len(all_tickers())} | Private names: "
          f"{sum(1 for c in COMPANIES if not c['ticker'])}")
