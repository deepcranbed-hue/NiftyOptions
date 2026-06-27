export const CONFIG = {
  strike_step: 50,
  top_n_walls: 4,
  fresh_oi_min: 5.0,
  pcr_heavy_put: 1.30,
  pcr_heavy_call: 0.75,
  lot_size: 65,
  wing_width: 200,
  iv_floor: 8.0,
  iv_cap: 18.0,
  accel_band: 250,
  accel_thresh_pct: 100.0,
  accel_full_hits: 5,
  iv_weight: 0.6,
  colors: {
    call: "#D85A30",
    put: "#1D9E75",
    spot: "#3B82F6",
    maxPain: "#EAB308",
  },
  tone_icon: {
    bull: "🟢",
    bear: "🔴",
    neutral: "⚪",
    caution: "🟡",
  },
};

export const SAMPLE_CHAIN = `-1%	0.1	1505.00	22700	24.7	3.60	17.5	-17%
-1.1%	0.4	1415.00	22800	23.6	3.95	28.2	-21%
-0.7%	0.2	1312.65	22900	22.3	4.30	22.8	-14%
-1.6%	11.6	1206.20	23000	21.2	4.80	95.3	10%
-11%	1.1	1108.00	23100	20.0	5.30	31.3	12%
-4.2%	5.5	1006.35	23200	18.9	6.20	63.9	40%
-0.1%	5.3	906.90	23300	17.6	6.90	47.3	-13%
-8%	4.5	811.35	23400	16.4	8.05	53.6	-15%
-0.8%	32.8	709.75	23500	15.2	9.45	105.3	-1.3%
-2.9%	10.6	612.60	23600	14.0	11.40	68.0	21%
-10%	7.0	517.00	23700	13.0	15.10	47.1	-20%
-10%	26.8	422.25	23800	12.1	20.95	87.9	18%
-9%	24.5	333.00	23900	11.2	29.75	88.4	5%
-12%	91.6	247.00	24000	10.3	44.00	153.6	34%
0.1%	65.4	171.00	24100	9.7	67.90	119.1	282%
109%	46.8	139.30	24150	9.5	85.15	70.3	1207%
57%	97.9	110.30	24200	9.3	106.40	91.7	379%
10%	70.8	64.55	24300	9.3	161.70	23.2	165%
12%	53.5	35.75	24400	9.3	232.90	6.6	149%
9%	114.2	19.45	24500	9.5	316.90	18.1	8%
4.1%	42.3	10.50	24600	9.8	406.90	2.9	0.2%
4.1%	39.5	6.15	24700	10.4	503.50	2.5	7%
41%	51.8	3.80	24800	11.0	602.75	2.0	-18%
16%	6.3	2.55	24950	12.4	743.00	0.0	0%`;

export const GLOBAL_MAP: Record<string, { sector: string; inverse: boolean; defaultPct: number }> = {
  "Nasdaq": { sector: "Nifty IT", inverse: false, defaultPct: +1.2 },
  "SOX (semis)": { sector: "Nifty IT", inverse: false, defaultPct: +2.1 },
  "S&P 500": { sector: "Broad / FII risk", inverse: false, defaultPct: +0.6 },
  "Kospi": { sector: "Nifty IT (Samsung/Hynix)", inverse: false, defaultPct: -0.4 },
  "Nikkei": { sector: "Broad Asia risk", inverse: false, defaultPct: +0.8 },
  "Hang Seng": { sector: "Nifty Metal", inverse: false, defaultPct: -1.5 },
  "CSI 300": { sector: "Nifty Metal / commod", inverse: false, defaultPct: -0.9 },
  "DAX": { sector: "Nifty Auto / industl", inverse: false, defaultPct: +0.3 },
  "Brent": { sector: "Energy / import bill", inverse: true, defaultPct: +1.4 },
  "Dollar (DXY)": { sector: "FII flows / EM", inverse: true, defaultPct: -0.2 },
  "USDINR": { sector: "FII vs IT exporters", inverse: true, defaultPct: +0.1 },
};

export const NEWS_LEXICON = {
  bull: [
    "expansion", "capex", "new plant", "order", "contract", "wins", "pli", "incentive",
    "subsidy", "fdi", "jv", "joint venture", "acquisition", "merger", "upgrade", "record",
    "launch", "approval", "clearance", "capacity", "demand surge", "buyback", "infrastructure",
    "budget allocation", "government push", "partnership", "stake buy"
  ],
  bear: [
    "probe", "investigation", "raid", "ban", "penalty", "fine", "downgrade", "cut guidance",
    "miss", "layoff", "shutdown", "recall", "lawsuit", "default", "npa", "fraud", "resign",
    "promoter sell", "pledge", "dilution", "qip", "duty hike", "tariff", "slowdown",
    "margin pressure", "weak demand"
  ],
};

export const SECTOR_KEYWORDS: Record<string, string[]> = {
  "IT": ["infosys", "tcs", "wipro", "hcl", "tech mahindra", "ltimindtree", "software", " it ", "ai "],
  "Banks/Fin": ["bank", "hdfc", "icici", "sbi", "axis", "kotak", "nbfc", "finance", "microfinance"],
  "Auto": ["auto", "maruti", "tata motors", "mahindra", "bajaj", "hero", "eicher", " ev"],
  "Metal": ["steel", "jsw", "tata steel", "hindalco", "vedanta", "metal", "copper", "aluminium"],
  "Energy": ["reliance", "ongc", "oil", "gas", "ioc", "bpcl", "hpcl", "power", "ntpc", "coal"],
  "Pharma": ["pharma", "cipla", "sun pharma", "dr reddy", "divis", "drug", "usfda"],
  "FMCG": ["fmcg", "hul", "itc", "nestle", "britannia", "dabur"],
  "Realty": ["realty", "dlf", "real estate", "property", "housing"],
  "Defence": ["defence", "defense", "hal", "bel", "bdl", "shipbuild"],
  "Telecom": ["airtel", "jio", "vodafone", "telecom"],
};

export const SAMPLE_NEWS = `Infosys AI revenue hits $1bn run rate, signals new deal wins
Government clears large defence order; HAL, BEL to benefit
JSW Steel pressured as weak global metal prices, China demand drag
Fed signals possible rate hike; US tech routs overnight
Reliance announces capex for new energy expansion
ICICI Bank leads private bank rally on strong credit growth`;
