import { CONFIG, GLOBAL_MAP, NEWS_LEXICON, SECTOR_KEYWORDS } from './constants';
import {
  AutomatedRead,
  ComplacencyMetrics,
  GlobalCueItem,
  MarketTone,
  NewsHeadline,
  OptionLeg,
  OptionRow,
  PayoffPoint,
  StrategyRecommendation,
  StructureContextItem,
} from '../types';

function num(val: string): number | null {
  const clean = val.trim().replace(/%/g, '').replace(/,/g, '');
  if (clean === '-' || clean === '') return NaN;
  const parsed = parseFloat(clean);
  return isNaN(parsed) ? null : parsed;
}

export function parseChain(text: string): OptionRow[] {
  const lines = text.trim().split(/\r?\n/);
  const rows: OptionRow[] = [];

  for (const line of lines) {
    if (!line.trim()) continue;
    const cells = line.replace(/\t/g, ' ').split(/\s+/);
    if (cells.length < 8) continue;

    // Take first 8 or exact 8
    const sliced = cells.slice(0, 8);
    const vals = sliced.map(num);
    if (vals.some((v) => v === null)) continue;

    rows.push({
      call_oichg: vals[0]!,
      call_oi: vals[1]!,
      call_ltp: vals[2]!,
      strike: vals[3]!,
      iv: vals[4]!,
      put_ltp: vals[5]!,
      put_oi: vals[6]!,
      put_oichg: vals[7]!,
    });
  }

  if (rows.length === 0) {
    throw new Error("No clean 8-column strike grid found — ensure columns are: CallOIChg% | CallOI(lakh) | CallLTP | Strike | IV | PutLTP | PutOI(lakh) | PutOIChg%");
  }

  return rows.sort((a, b) => a.strike - b.strike);
}

export function estimateSpot(rows: OptionRow[]): number {
  const valid = rows.filter((r) => !isNaN(r.call_ltp) && !isNaN(r.put_ltp));
  if (valid.length === 0) return rows[Math.floor(rows.length / 2)].strike;

  let minDiff = Infinity;
  let bestRow = valid[0];

  for (const r of valid) {
    const diff = Math.abs(r.call_ltp - r.put_ltp);
    if (diff < minDiff) {
      minDiff = diff;
      bestRow = r;
    }
  }

  return bestRow.strike + bestRow.call_ltp - bestRow.put_ltp;
}

export function calculateMaxPain(rows: OptionRow[]): number {
  const strikes = rows.map((r) => r.strike);
  let minLoss = Infinity;
  let maxPainStrike = strikes[0] || 24000;

  for (const exp of strikes) {
    let totalLoss = 0;
    for (const r of rows) {
      // Call writers loss if expiration > strike
      const callLoss = !isNaN(r.call_oi) ? r.call_oi * Math.max(0, exp - r.strike) : 0;
      // Put writers loss if expiration < strike
      const putLoss = !isNaN(r.put_oi) ? r.put_oi * Math.max(0, r.strike - exp) : 0;
      totalLoss += callLoss + putLoss;
    }
    if (totalLoss < minLoss) {
      minLoss = totalLoss;
      maxPainStrike = exp;
    }
  }

  return maxPainStrike;
}

export function calculateATM(rows: OptionRow[], spot: number) {
  let closest = rows[0] || { strike: spot, iv: 12.0, call_ltp: 100, put_ltp: 100 };
  let minDiff = Infinity;

  for (const r of rows) {
    const diff = Math.abs(r.strike - spot);
    if (diff < minDiff) {
      minDiff = diff;
      closest = r;
    }
  }

  const straddle = (isNaN(closest.call_ltp) ? 0 : closest.call_ltp) + (isNaN(closest.put_ltp) ? 0 : closest.put_ltp);
  return {
    strike: closest.strike,
    iv: isNaN(closest.iv) ? 14.0 : closest.iv,
    straddle,
  };
}

export function calculatePCR(rows: OptionRow[]): number {
  let calls = 0;
  let puts = 0;
  for (const r of rows) {
    if (!isNaN(r.call_oi)) calls += r.call_oi;
    if (!isNaN(r.put_oi)) puts += r.put_oi;
  }
  return calls > 0 ? puts / calls : 1.0;
}

export function generateReads(
  rows: OptionRow[],
  spot: number,
  maxPain: number,
  atmMeta: { straddle: number }
): { reads: AutomatedRead[]; resRow: OptionRow; supRow: OptionRow } {
  let maxCallOI = -1;
  let resRow = rows[0]!;
  let maxPutOI = -1;
  let supRow = rows[0]!;

  for (const r of rows) {
    if (!isNaN(r.call_oi) && r.call_oi > maxCallOI) {
      maxCallOI = r.call_oi;
      resRow = r;
    }
    if (!isNaN(r.put_oi) && r.put_oi > maxPutOI) {
      maxPutOI = r.put_oi;
      supRow = r;
    }
  }

  const out: AutomatedRead[] = [];

  out.push({
    tone: 'bear',
    text: `${resRow.strike}: Heaviest Call OI (${resRow.call_oi.toFixed(1)}L) → Primary writers' overhead resistance ceiling.`,
  });

  if (spot < supRow.strike) {
    out.push({
      tone: 'caution',
      text: `${supRow.strike}: Top Put wall sits ABOVE live spot (${Math.round(spot)}) → Key support breached, put writers caught offside.`,
    });
  } else {
    out.push({
      tone: 'bull',
      text: `${supRow.strike}: Heaviest Put OI (${supRow.put_oi.toFixed(1)}L) → Primary writers' structural support floor.`,
    });
  }

  const mpDiff = Math.round(maxPain - spot);
  const tiltText = mpDiff > 50 ? 'upward magnet pull' : mpDiff < -50 ? 'downward gravity drag' : 'flat equilibrium';
  out.push({
    tone: 'neutral',
    text: `Max Pain ${maxPain} vs Spot ${Math.round(spot)} (${mpDiff > 0 ? '+' : ''}${mpDiff}) → Structural ${tiltText}; effective on quiet-tape regimes.`,
  });

  const pcrVal = calculatePCR(rows);
  const pcrTone = pcrVal >= CONFIG.pcr_heavy_put ? 'bull' : pcrVal <= CONFIG.pcr_heavy_call ? 'bear' : 'neutral';
  const pcrDesc = pcrVal >= CONFIG.pcr_heavy_put ? 'put-heavy & supportive' : pcrVal <= CONFIG.pcr_heavy_call ? 'call-heavy & upside capped' : 'balanced structure';
  out.push({
    tone: pcrTone,
    text: `PCR (OI) ${pcrVal.toFixed(2)} → Market structure is ${pcrDesc}.`,
  });

  if (atmMeta.straddle > 0) {
    const lowR = Math.round(spot - atmMeta.straddle);
    const highR = Math.round(spot + atmMeta.straddle);
    out.push({
      tone: 'neutral',
      text: `ATM Straddle Premium ₹${Math.round(atmMeta.straddle)} → Implied 1-SD expiration bounds: ${lowR} – ${highR}.`,
    });
  }

  return { reads: out, resRow, supRow };
}

export function generateStructureContext(resRow: OptionRow, supRow: OptionRow): StructureContextItem[] {
  const w = CONFIG.wing_width;
  return [
    {
      label: "Range Structure",
      text: `Iron Condor setup: Sell ~${supRow.strike} PE / ${resRow.strike} CE | Buy protective wings at ${supRow.strike - w} PE / ${resRow.strike + w} CE.`,
    },
    {
      label: "Upside Capping",
      text: `Bear Call Spread: Sell ${resRow.strike} CE / Buy ${resRow.strike + w} CE to monetize resistance overhead.`,
    },
    {
      label: "Downside Floor",
      text: `Bull Put Spread: Sell ${supRow.strike} PE / Buy ${supRow.strike - w} PE to capture put-writing support.`,
    },
  ];
}

export function calculateComplacency(rows: OptionRow[], spot: number, atmIV: number): ComplacencyMetrics {
  const iv_pct = Math.min(1, Math.max(0, (atmIV - CONFIG.iv_floor) / (CONFIG.iv_cap - CONFIG.iv_floor)));
  const comp_iv = (1 - iv_pct) * 100;

  const nearRows = rows.filter((r) => Math.abs(r.strike - spot) <= CONFIG.accel_band);
  const bursts = nearRows.filter((r) => !isNaN(r.put_oichg) && r.put_oichg > CONFIG.accel_thresh_pct);
  const accel = Math.min(1, Math.max(0, bursts.length / CONFIG.accel_full_hits)) * 100;

  let max_burst = 0;
  for (const r of nearRows) {
    if (!isNaN(r.put_oichg) && r.put_oichg > max_burst) max_burst = r.put_oichg;
  }

  const score = Math.round(CONFIG.iv_weight * comp_iv + (1 - CONFIG.iv_weight) * accel);

  let verdict: { tone: MarketTone; msg: string };
  if (score >= 65) {
    verdict = {
      tone: 'caution',
      msg: "Extreme Complacency — Vol is cheap into live tail risk; asymmetry strongly favors OWNING optionality (Long Debit Spreads / Strangles), not selling it.",
    };
  } else if (score >= 40) {
    verdict = {
      tone: 'neutral',
      msg: "Elevated Complacency — Premium selling pays less per unit of tail risk; strictly define wing risk.",
    };
  } else {
    verdict = {
      tone: 'neutral',
      msg: "Vol within normal regime — Premium selling is adequately compensated by IV decay.",
    };
  }

  return {
    iv: atmIV,
    comp_iv,
    accel,
    bursts: bursts.length,
    max_burst,
    score,
    verdict,
  };
}

export function generateGlobalCues(pctMap: Record<string, number>): GlobalCueItem[] {
  const items: GlobalCueItem[] = [];

  for (const [name, pct] of Object.entries(pctMap)) {
    if (pct === undefined || isNaN(pct)) continue;
    const cfg = GLOBAL_MAP[name];
    if (!cfg) continue;

    const bullishForIndia = (pct > 0) !== cfg.inverse;
    let tone: MarketTone = 'neutral';
    if (Math.abs(pct) > 0.05) {
      tone = bullishForIndia ? 'bull' : 'bear';
    }
    const arrow = bullishForIndia ? 'tailwind' : 'headwind';

    items.push({
      name,
      pct,
      sector: cfg.sector,
      inverse: cfg.inverse,
      tone,
      arrow,
      read: `${name} ${pct > 0 ? '+' : ''}${pct.toFixed(2)}% → Immediate ${arrow} for ${cfg.sector}.`,
    });
  }

  return items;
}



// ==========================================
// OPTION STRATEGY ENGINE & PAYOFF CALCULATOR
// ==========================================

function getNearestStrike(spot: number, offsetStep: number = 0): number {
  const base = Math.round(spot / CONFIG.strike_step) * CONFIG.strike_step;
  return base + offsetStep * CONFIG.strike_step;
}

function getOptionPrice(rows: OptionRow[], strike: number, type: 'CE' | 'PE'): number {
  const found = rows.find((r) => r.strike === strike);
  if (found) {
    const ltp = type === 'CE' ? found.call_ltp : found.put_ltp;
    if (!isNaN(ltp) && ltp > 0) return ltp;
  }
  // Theoretical estimate if missing in chain
  const rowsSpot = rows.length > 0 ? estimateSpot(rows) : 24000;
  const diff = Math.abs(strike - rowsSpot);
  return Math.max(5, Math.round(180 - diff * 0.45));
}

export function suggestStrategies(
  rows: OptionRow[],
  spot: number,
  outlook: 'bullish' | 'bearish' | 'neutral' | 'volatile',
  ivEnv: 'low' | 'moderate' | 'high',
  lotSize: number = 65
): StrategyRecommendation[] {
  const atmStrike = getNearestStrike(spot, 0);
  const wing = 200; // 4 strikes

  const allStrategies: StrategyRecommendation[] = [];

  // 1. Iron Condor
  const icPutSell = getNearestStrike(spot, -3); // -150
  const icPutBuy = icPutSell - wing;
  const icCallSell = getNearestStrike(spot, +3); // +150
  const icCallBuy = icCallSell + wing;

  const icLegs: OptionLeg[] = [
    { action: 'SELL', type: 'PE', strike: icPutSell, premium: getOptionPrice(rows, icPutSell, 'PE'), qtyRatio: 1 },
    { action: 'BUY', type: 'PE', strike: icPutBuy, premium: getOptionPrice(rows, icPutBuy, 'PE'), qtyRatio: 1 },
    { action: 'SELL', type: 'CE', strike: icCallSell, premium: getOptionPrice(rows, icCallSell, 'CE'), qtyRatio: 1 },
    { action: 'BUY', type: 'CE', strike: icCallBuy, premium: getOptionPrice(rows, icCallBuy, 'CE'), qtyRatio: 1 },
  ];
  const icNet = icLegs[0].premium - icLegs[1].premium + icLegs[2].premium - icLegs[3].premium;

  allStrategies.push({
    id: 'iron_condor',
    name: 'Iron Condor (Defined Risk)',
    outlook: 'neutral',
    ivEnvironment: 'moderate',
    riskProfile: 'Defined Risk',
    netPremium: -icNet, // credit
    maxProfit: `₹${Math.round(icNet  * lotSize).toLocaleString()}`,
    maxLoss: `₹${Math.round((wing - icNet) * lotSize).toLocaleString()}`,
    breakevens: [icPutSell - icNet, icCallSell + icNet],
    probabilityOfProfit: 72,
    rationale: "Captures rapid time decay (Theta) in range-bound regimes. Wings cap catastrophe black-swan risk.",
    adjustmentRule: "If tested on call side, roll the put spread up to collect additional credit.",
    legs: icLegs,
  });

  // 2. Short Strangle
  const stLegs: OptionLeg[] = [
    { action: 'SELL', type: 'PE', strike: icPutSell, premium: getOptionPrice(rows, icPutSell, 'PE'), qtyRatio: 1 },
    { action: 'SELL', type: 'CE', strike: icCallSell, premium: getOptionPrice(rows, icCallSell, 'CE'), qtyRatio: 1 },
  ];
  const stNet = stLegs[0].premium + stLegs[1].premium;
  allStrategies.push({
    id: 'short_strangle',
    name: 'Short Strangle (High Probability)',
    outlook: 'neutral',
    ivEnvironment: 'high',
    riskProfile: 'Undefined Risk',
    netPremium: -stNet,
    maxProfit: `₹${Math.round(stNet  * lotSize).toLocaleString()}`,
    maxLoss: 'Unlimited',
    breakevens: [icPutSell - stNet, icCallSell + stNet],
    probabilityOfProfit: 81,
    rationale: "High IV rank play. Widest breakevens allow maximum room for spot oscillations.",
    adjustmentRule: "Maintain delta neutrality by rolling the untested strike inward if spot breaks 1-SD.",
    legs: stLegs,
  });

  // 3. Bull Put Spread
  const bpsSell = getNearestStrike(spot, -1);
  const bpsBuy = bpsSell - wing;
  const bpsLegs: OptionLeg[] = [
    { action: 'SELL', type: 'PE', strike: bpsSell, premium: getOptionPrice(rows, bpsSell, 'PE'), qtyRatio: 1 },
    { action: 'BUY', type: 'PE', strike: bpsBuy, premium: getOptionPrice(rows, bpsBuy, 'PE'), qtyRatio: 1 },
  ];
  const bpsNet = bpsLegs[0].premium - bpsLegs[1].premium;
  allStrategies.push({
    id: 'bull_put_spread',
    name: 'Bull Put Credit Spread',
    outlook: 'bullish',
    ivEnvironment: 'moderate',
    riskProfile: 'Defined Risk',
    netPremium: -bpsNet,
    maxProfit: `₹${Math.round(bpsNet  * lotSize).toLocaleString()}`,
    maxLoss: `₹${Math.round((wing - bpsNet) * lotSize).toLocaleString()}`,
    breakevens: [bpsSell - bpsNet],
    probabilityOfProfit: 68,
    rationale: "Bullish structure capturing put writer support wall. Profits if Nifty stays flat or ascends.",
    adjustmentRule: "If Nifty falls below sold put, convert to Iron Fly or close for 2x credit loss.",
    legs: bpsLegs,
  });

  // 4. Bull Call Spread
  const bcsBuy = atmStrike;
  const bcsSell = bcsBuy + wing;
  const bcsLegs: OptionLeg[] = [
    { action: 'BUY', type: 'CE', strike: bcsBuy, premium: getOptionPrice(rows, bcsBuy, 'CE'), qtyRatio: 1 },
    { action: 'SELL', type: 'CE', strike: bcsSell, premium: getOptionPrice(rows, bcsSell, 'CE'), qtyRatio: 1 },
  ];
  const bcsNet = bcsLegs[0].premium - bcsLegs[1].premium;
  allStrategies.push({
    id: 'bull_call_spread',
    name: 'Bull Call Debit Spread',
    outlook: 'bullish',
    ivEnvironment: 'low',
    riskProfile: 'Defined Risk',
    netPremium: bcsNet, // debit
    maxProfit: `₹${Math.round((wing - bcsNet) * lotSize).toLocaleString()}`,
    maxLoss: `₹${Math.round(bcsNet  * lotSize).toLocaleString()}`,
    breakevens: [bcsBuy + bcsNet],
    probabilityOfProfit: 54,
    rationale: "Low IV bullish momentum trade. Sold call reduces net cost and neutralizes vega drag.",
    adjustmentRule: "Close at 50% max profit target or 7 DTE.",
    legs: bcsLegs,
  });

  // 5. Bear Call Spread
  const bcs2Sell = getNearestStrike(spot, +1);
  const bcs2Buy = bcs2Sell + wing;
  const bcs2Legs: OptionLeg[] = [
    { action: 'SELL', type: 'CE', strike: bcs2Sell, premium: getOptionPrice(rows, bcs2Sell, 'CE'), qtyRatio: 1 },
    { action: 'BUY', type: 'CE', strike: bcs2Buy, premium: getOptionPrice(rows, bcs2Buy, 'CE'), qtyRatio: 1 },
  ];
  const bcs2Net = bcs2Legs[0].premium - bcs2Legs[1].premium;
  allStrategies.push({
    id: 'bear_call_spread',
    name: 'Bear Call Credit Spread',
    outlook: 'bearish',
    ivEnvironment: 'moderate',
    riskProfile: 'Defined Risk',
    netPremium: -bcs2Net,
    maxProfit: `₹${Math.round(bcs2Net  * lotSize).toLocaleString()}`,
    maxLoss: `₹${Math.round((wing - bcs2Net) * lotSize).toLocaleString()}`,
    breakevens: [bcs2Sell + bcs2Net],
    probabilityOfProfit: 67,
    rationale: "Monetizes heavy call OI ceiling. Profits if Nifty drifts lower or stays below resistance.",
    adjustmentRule: "Roll down call spread if Nifty breaks support.",
    legs: bcs2Legs,
  });

  // 6. Bear Put Spread
  const bps2Buy = atmStrike;
  const bps2Sell = bps2Buy - wing;
  const bps2Legs: OptionLeg[] = [
    { action: 'BUY', type: 'PE', strike: bps2Buy, premium: getOptionPrice(rows, bps2Buy, 'PE'), qtyRatio: 1 },
    { action: 'SELL', type: 'PE', strike: bps2Sell, premium: getOptionPrice(rows, bps2Sell, 'PE'), qtyRatio: 1 },
  ];
  const bps2Net = bps2Legs[0].premium - bps2Legs[1].premium;
  allStrategies.push({
    id: 'bear_put_spread',
    name: 'Bear Put Debit Spread',
    outlook: 'bearish',
    ivEnvironment: 'low',
    riskProfile: 'Defined Risk',
    netPremium: bps2Net,
    maxProfit: `₹${Math.round((wing - bps2Net) * lotSize).toLocaleString()}`,
    maxLoss: `₹${Math.round(bps2Net  * lotSize).toLocaleString()}`,
    breakevens: [bps2Buy - bps2Net],
    probabilityOfProfit: 52,
    rationale: "Sharp downside breakdown play. Limited risk with attractive 2:1 risk-reward profile.",
    adjustmentRule: "Take profit near major put wall support.",
    legs: bps2Legs,
  });

  // 7. Long Straddle
  const lsdLegs: OptionLeg[] = [
    { action: 'BUY', type: 'CE', strike: atmStrike, premium: getOptionPrice(rows, atmStrike, 'CE'), qtyRatio: 1 },
    { action: 'BUY', type: 'PE', strike: atmStrike, premium: getOptionPrice(rows, atmStrike, 'PE'), qtyRatio: 1 },
  ];
  const lsdNet = lsdLegs[0].premium + lsdLegs[1].premium;
  allStrategies.push({
    id: 'long_straddle',
    name: 'Long Straddle (Vol Expansion)',
    outlook: 'volatile',
    ivEnvironment: 'low',
    riskProfile: 'Defined Risk',
    netPremium: lsdNet,
    maxProfit: 'Unlimited',
    maxLoss: `₹${Math.round(lsdNet  * lotSize).toLocaleString()}`,
    breakevens: [atmStrike - lsdNet, atmStrike + lsdNet],
    probabilityOfProfit: 44,
    rationale: "Pure long gamma & vega explosion setup prior to major event catalyst (RBI/Fed/Budget).",
    adjustmentRule: "Scalp gamma by trimming profitable leg on 100pt directional spikes.",
    legs: lsdLegs,
  });

  // 8. Iron Butterfly
  const ibLegs: OptionLeg[] = [
    { action: 'BUY', type: 'PE', strike: atmStrike - wing, premium: getOptionPrice(rows, atmStrike - wing, 'PE'), qtyRatio: 1 },
    { action: 'SELL', type: 'PE', strike: atmStrike, premium: getOptionPrice(rows, atmStrike, 'PE'), qtyRatio: 1 },
    { action: 'SELL', type: 'CE', strike: atmStrike, premium: getOptionPrice(rows, atmStrike, 'CE'), qtyRatio: 1 },
    { action: 'BUY', type: 'CE', strike: atmStrike + wing, premium: getOptionPrice(rows, atmStrike + wing, 'CE'), qtyRatio: 1 },
  ];
  const ibNet = ibLegs[1].premium + ibLegs[2].premium - ibLegs[0].premium - ibLegs[3].premium;
  allStrategies.push({
    id: 'iron_butterfly',
    name: 'Iron Butterfly (Pinning Play)',
    outlook: 'neutral',
    ivEnvironment: 'high',
    riskProfile: 'Defined Risk',
    netPremium: -ibNet,
    maxProfit: `₹${Math.round(ibNet  * lotSize).toLocaleString()}`,
    maxLoss: `₹${Math.round((wing - ibNet) * lotSize).toLocaleString()}`,
    breakevens: [atmStrike - ibNet, atmStrike + ibNet],
    probabilityOfProfit: 62,
    rationale: "Aggressive max pain pinning trade for expiry day. Collects maximum ATM credit.",
    adjustmentRule: "Close position before 2 PM on expiry day to avoid gamma assignment risk.",
    legs: ibLegs,
  });

  // Sort by match score
  return allStrategies.sort((a, b) => {
    let scoreA = 0;
    let scoreB = 0;
    if (a.outlook === outlook) scoreA += 50;
    if (b.outlook === outlook) scoreB += 50;
    if (a.ivEnvironment === ivEnv) scoreA += 30;
    if (b.ivEnvironment === ivEnv) scoreB += 30;
    return scoreB - scoreA;
  });
}

export function calculatePayoffCurve(legs: OptionLeg[], spot: number, lotSize: number = 65): PayoffPoint[] {
  const minPrice = Math.round((spot * 0.94) / 25) * 25;
  const maxPrice = Math.round((spot * 1.06) / 25) * 25;
  const step = 25;
  const points: PayoffPoint[] = [];

  for (let p = minPrice; p <= maxPrice; p += step) {
    let netPnl = 0;

    for (const leg of legs) {
      let intrinsic = 0;
      if (leg.type === 'CE') {
        intrinsic = Math.max(0, p - leg.strike);
      } else {
        intrinsic = Math.max(0, leg.strike - p);
      }

      if (leg.action === 'BUY') {
        netPnl += (intrinsic - leg.premium) * leg.qtyRatio * lotSize; // Lot size 25
      } else {
        netPnl += (leg.premium - intrinsic) * leg.qtyRatio * lotSize;
      }
    }

    points.push({
      price: p,
      pnl: Math.round(netPnl),
      isSpot: Math.abs(p - spot) < step / 2,
    });
  }

  return points;
}
