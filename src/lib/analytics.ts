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

    const call_oichg_abs = vals[0]!;
    const call_oi = vals[1]!;
    const put_oi = vals[6]!;
    const put_oichg_abs = vals[7]!;

    const prev_call_oi = call_oi - call_oichg_abs;
    const call_oichg_pct = (prev_call_oi > 0) ? Number(((call_oichg_abs / prev_call_oi) * 100).toFixed(1)) : 0;

    const prev_put_oi = put_oi - put_oichg_abs;
    const put_oichg_pct = (prev_put_oi > 0) ? Number(((put_oichg_abs / prev_put_oi) * 100).toFixed(1)) : 0;

    rows.push({
      call_oichg: call_oichg_pct,
      call_oi: call_oi,
      call_ltp: vals[2]!,
      strike: vals[3]!,
      iv: vals[4]!,
      put_ltp: vals[5]!,
      put_oi: put_oi,
      put_oichg: put_oichg_pct,
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

  // OI FLOW read — qualifies the static support/resistance above with whether those
  // walls are being built (writing) or pulled (unwinding), so "support floor" isn't
  // read as solid when it is actively eroding.
  const oiFlow = analyzeOIFlow(rows, spot);
  const flowTone: MarketTone =
    oiFlow.put_flow === 'unwinding' && oiFlow.call_flow === 'unwinding' ? 'caution'
    : oiFlow.put_flow === 'writing' && oiFlow.call_flow !== 'writing' ? 'bull'
    : oiFlow.call_flow === 'writing' && oiFlow.put_flow !== 'writing' ? 'bear'
    : 'neutral';
  out.push({ tone: flowTone, text: `OI Flow → ${oiFlow.read}` });

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

// ── OI FLOW: is positioning being WRITTEN (walls firming) or UNWOUND (walls pulled)? ──
// The old "Max Put build" bug picked the single strike with the largest % OI increase,
// so a ~2k-contract blip on an illiquid far strike (e.g. 24550 above spot) beat a
// 900k-contract move at ATM. This is liquidity-weighted instead: we measure the signed
// CONTRACTS added at each strike as OI × %change, restrict "put build" to the support
// zone (strike ≤ spot) and "call build" to resistance (strike ≥ spot), and net the
// near-spot band so we can tell writing from unwinding.
export interface OIFlow {
  net_put: number;
  net_call: number;
  put_flow: 'writing' | 'unwinding' | 'flat';
  call_flow: 'writing' | 'unwinding' | 'flat';
  put_build_strike?: number;
  call_build_strike?: number;
  summary: string;
  read: string;
}

const OI_FLOW_MIN = 100000; // ≥ ~1 lakh net contracts to count as a real flow (liquidity floor)

function fmtOI(n: number): string {
  const a = Math.abs(n);
  const s = n < 0 ? '-' : '+';
  if (a >= 1e7) return `${s}${(a / 1e7).toFixed(1)}Cr`;
  return `${s}${(a / 1e5).toFixed(1)}L`;
}

export function analyzeOIFlow(rows: OptionRow[], spot: number, band = CONFIG.accel_band): OIFlow {
  let netPut = 0, netCall = 0;
  let bestPut = { strike: 0, c: 0 }, bestCall = { strike: 0, c: 0 };
  let maxPutOI = { strike: 0, oi: -1 }, maxCallOI = { strike: 0, oi: -1 };
  for (const r of rows) {
    // signed contracts added ≈ OI level × %change — naturally liquidity-weighted.
    const pAdd = (isFinite(r.put_oi) && isFinite(r.put_oichg)) ? r.put_oi * (r.put_oichg / 100) : 0;
    const cAdd = (isFinite(r.call_oi) && isFinite(r.call_oichg)) ? r.call_oi * (r.call_oichg / 100) : 0;
    if (Math.abs(r.strike - spot) <= band) { netPut += pAdd; netCall += cAdd; }
    if (r.strike <= spot && pAdd > bestPut.c) bestPut = { strike: r.strike, c: pAdd };   // support-zone put writing
    if (r.strike >= spot && cAdd > bestCall.c) bestCall = { strike: r.strike, c: cAdd };  // resistance-zone call writing
    if (r.put_oi > maxPutOI.oi) maxPutOI = { strike: r.strike, oi: r.put_oi };
    if (r.call_oi > maxCallOI.oi) maxCallOI = { strike: r.strike, oi: r.call_oi };
  }
  const flow = (n: number): 'writing' | 'unwinding' | 'flat' =>
    n > OI_FLOW_MIN ? 'writing' : n < -OI_FLOW_MIN ? 'unwinding' : 'flat';
  const put_flow = flow(netPut), call_flow = flow(netCall);
  const put_build_strike = bestPut.c > OI_FLOW_MIN ? bestPut.strike : undefined;
  const call_build_strike = bestCall.c > OI_FLOW_MIN ? bestCall.strike : undefined;

  const putPhrase = put_flow === 'writing'
    ? `put writers are ADDING support${put_build_strike ? ` (heaviest fresh writing at ${put_build_strike})` : ''} — the floor is firming`
    : put_flow === 'unwinding'
    ? `put writers are COVERING near spot (net ${fmtOI(netPut)} contracts) — support is being pulled`
    : `put OI is broadly flat near spot`;
  const callPhrase = call_flow === 'writing'
    ? `call writers are ADDING resistance${call_build_strike ? ` (heaviest at ${call_build_strike})` : ''} — upside is being capped`
    : call_flow === 'unwinding'
    ? `call writers are COVERING (net ${fmtOI(netCall)} contracts) — resistance is easing`
    : `call OI is broadly flat`;

  let net: string;
  if (put_flow === 'unwinding' && call_flow === 'unwinding')
    net = 'Both sides are unwinding — positioning is being CUT and the range walls are weakening; favor a range break over mean-reversion.';
  else if (put_flow === 'writing' && call_flow === 'writing')
    net = 'Both sides are writing — the range is being REINFORCED; mean-reversion between the walls is favored.';
  else if (put_flow === 'writing' && call_flow !== 'writing')
    net = 'Puts written while calls ease — a supportive/bullish tilt.';
  else if (call_flow === 'writing' && put_flow !== 'writing')
    net = 'Calls written while puts ease — an upside-capped/bearish tilt.';
  else
    net = 'No decisive OI flow — positioning is roughly steady.';

  const read = `${putPhrase[0].toUpperCase()}${putPhrase.slice(1)}; ${callPhrase}. ${net}`;
  const summary = `Static walls: put support ${maxPutOI.strike}, call resistance ${maxCallOI.strike}. ${read}`;
  return { net_put: netPut, net_call: netCall, put_flow, call_flow, put_build_strike, call_build_strike, summary, read };
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

  let max_put_oi_strike = 0;
  let max_put_oi = -1;
  let max_call_oi_strike = 0;
  let max_call_oi = -1;

  let max_put_oichg_strike = 0;
  let max_put_oichg = -1;
  let max_call_oichg_strike = 0;
  let max_call_oichg = -1;

  for (const r of rows) {
    if (r.put_oi > max_put_oi) {
      max_put_oi = r.put_oi;
      max_put_oi_strike = r.strike;
    }
    if (r.call_oi > max_call_oi) {
      max_call_oi = r.call_oi;
      max_call_oi_strike = r.strike;
    }
    if (r.put_oichg && r.put_oichg > max_put_oichg) {
      max_put_oichg = r.put_oichg;
      max_put_oichg_strike = r.strike;
    }
    if (r.call_oichg && r.call_oichg > max_call_oichg) {
      max_call_oichg = r.call_oichg;
      max_call_oichg_strike = r.strike;
    }
  }

  const final_score = Math.round(CONFIG.iv_weight * comp_iv + (1 - CONFIG.iv_weight) * accel);
  const has_oi_data = nearRows.some((r) => !isNaN(r.put_oichg) && Math.abs(r.put_oichg) > 0.001);
  const oiFlow = analyzeOIFlow(rows, spot);

  return {
    score: final_score,
    iv: atmIV,
    comp_iv,
    accel,
    has_oi_data,
    bursts: bursts.length,
    max_burst,
    verdict,
    max_put_oi_strike,
    max_call_oi_strike,
    max_put_oichg_strike: max_put_oichg_strike || undefined,
    max_call_oichg_strike: max_call_oichg_strike || undefined,
    oi_flow_summary: has_oi_data ? oiFlow.summary : undefined,
    put_flow: has_oi_data ? oiFlow.put_flow : undefined,
    call_flow: has_oi_data ? oiFlow.call_flow : undefined,
    put_build_strike: oiFlow.put_build_strike
  };
}

export function generateGlobalCues(pctMap: Record<string, number>): GlobalCueItem[] {
  const items: GlobalCueItem[] = [];

  for (const [name, pct] of Object.entries(pctMap)) {
    if (pct === undefined || isNaN(pct)) continue;
    const cfg = GLOBAL_MAP[name];
    if (!cfg) continue;

    let tone: MarketTone = 'neutral';
    let arrow: 'tailwind' | 'headwind' | 'neutral' = 'neutral';
    
    if (Math.abs(pct) > 0.05) {
      const bullishForIndia = (pct > 0) !== cfg.inverse;
      tone = bullishForIndia ? 'bull' : 'bear';
      arrow = bullishForIndia ? 'tailwind' : 'headwind';
    }

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

export function evaluateStrategyMetrics(legs: OptionLeg[], lotSize: number = CONFIG.lot_size) {
  let netPremium = 0;
  let upsideSlope = 0;
  let downsideSlope = 0;

  for (const leg of legs) {
    if (leg.action === 'BUY') netPremium -= leg.premium * leg.qtyRatio;
    else netPremium += leg.premium * leg.qtyRatio;

    if (leg.type === 'CE') {
      upsideSlope += (leg.action === 'BUY' ? 1 : -1) * leg.qtyRatio;
    } else {
      downsideSlope += (leg.action === 'BUY' ? -1 : 1) * leg.qtyRatio;
    }
  }

  let maxPnl = -Infinity;
  let minPnl = Infinity;

  const strikes = legs.map(l => l.strike);
  const criticalPoints = [...strikes, 0, 100000];
  
  for (const p of criticalPoints) {
    let pnl = 0;
    for (const leg of legs) {
      const intrinsic = leg.type === 'CE' ? Math.max(0, p - leg.strike) : Math.max(0, leg.strike - p);
      pnl += (leg.action === 'BUY' ? intrinsic - leg.premium : leg.premium - intrinsic) * leg.qtyRatio * lotSize;
    }
    if (pnl > maxPnl) maxPnl = pnl;
    if (pnl < minPnl) minPnl = pnl;
  }

  const isProfitUnlimited = (upsideSlope > 0) || (downsideSlope < 0);
  const isLossUnlimited = (upsideSlope < 0) || (downsideSlope > 0);

  const maxProfit = isProfitUnlimited ? 'Unlimited' : `₹${Math.round(maxPnl).toLocaleString('en-IN')}`;
  const maxLoss = isLossUnlimited ? 'Unlimited' : `₹${Math.round(Math.abs(minPnl)).toLocaleString('en-IN')}`;

  const breakevens: number[] = [];
  let prevPnl = 0;
  let prevPrice = -1;
  const minSearch = Math.max(0, Math.min(...strikes) - 3000);
  const maxSearch = Math.max(...strikes) + 3000;

  for (let p = minSearch; p <= maxSearch; p += 5) {
    let pnl = 0;
    for (const leg of legs) {
      const intrinsic = leg.type === 'CE' ? Math.max(0, p - leg.strike) : Math.max(0, leg.strike - p);
      pnl += (leg.action === 'BUY' ? intrinsic - leg.premium : leg.premium - intrinsic) * leg.qtyRatio * lotSize;
    }
    if (prevPrice !== -1) {
      if ((prevPnl <= 0 && pnl > 0) || (prevPnl >= 0 && pnl < 0)) {
        const exact = prevPrice + Math.abs(prevPnl) / (Math.abs(pnl - prevPnl)) * 5;
        breakevens.push(exact);
      }
    }
    prevPnl = pnl;
    prevPrice = p;
  }

  return { maxProfit, maxLoss, netPremium, breakevens };
}

export function suggestStrategies(
  rows: OptionRow[],
  spot: number,
  outlook: 'bullish' | 'bearish' | 'neutral' | 'volatile',
  ivEnv: 'low' | 'moderate' | 'high',
  lotSize: number = CONFIG.lot_size,
  customStrikes?: Record<string, number[]>
): StrategyRecommendation[] {
  const atmStrike = getNearestStrike(spot, 0);
  const wing = 200; // 4 strikes

  const allStrategies: StrategyRecommendation[] = [];

  // Helper to construct leg
  const mkLeg = (action: 'BUY' | 'SELL', type: 'CE' | 'PE', strike: number): OptionLeg => ({
    action, type, strike, premium: getOptionPrice(rows, strike, type), qtyRatio: 1
  });

  // 1. Iron Condor
  const icPutSell = customStrikes?.['iron_condor']?.[0] ?? getNearestStrike(spot, -3);
  const icPutBuy = customStrikes?.['iron_condor']?.[1] ?? (icPutSell - wing);
  const icCallSell = customStrikes?.['iron_condor']?.[2] ?? getNearestStrike(spot, +3);
  const icCallBuy = customStrikes?.['iron_condor']?.[3] ?? (icCallSell + wing);
  const icLegs = [
    mkLeg('SELL', 'PE', icPutSell), mkLeg('BUY', 'PE', icPutBuy),
    mkLeg('SELL', 'CE', icCallSell), mkLeg('BUY', 'CE', icCallBuy)
  ];
  const icMetrics = evaluateStrategyMetrics(icLegs, lotSize);
  allStrategies.push({
    id: 'iron_condor', name: 'Iron Condor (Defined Risk)', outlook: 'neutral', ivEnvironment: 'moderate', riskProfile: 'Defined Risk',
    rationale: "Captures rapid time decay (Theta) in range-bound regimes. Wings cap catastrophe black-swan risk.",
    adjustmentRule: "If tested on call side, roll the put spread up to collect additional credit.",
    legs: icLegs, probabilityOfProfit: 72, ...icMetrics
  });

  // 2. Short Strangle
  const stPutSell = customStrikes?.['short_strangle']?.[0] ?? getNearestStrike(spot, -3);
  const stCallSell = customStrikes?.['short_strangle']?.[1] ?? getNearestStrike(spot, +3);
  const stLegs = [mkLeg('SELL', 'PE', stPutSell), mkLeg('SELL', 'CE', stCallSell)];
  const stMetrics = evaluateStrategyMetrics(stLegs, lotSize);
  allStrategies.push({
    id: 'short_strangle', name: 'Short Strangle (High Probability)', outlook: 'neutral', ivEnvironment: 'high', riskProfile: 'Undefined Risk',
    rationale: "High IV rank play. Widest breakevens allow maximum room for spot oscillations.",
    adjustmentRule: "Maintain delta neutrality by rolling the untested strike inward if spot breaks 1-SD.",
    legs: stLegs, probabilityOfProfit: 81, ...stMetrics
  });

  // 3. Bull Put Spread
  const bpsSell = customStrikes?.['bull_put_spread']?.[0] ?? getNearestStrike(spot, -1);
  const bpsBuy = customStrikes?.['bull_put_spread']?.[1] ?? (bpsSell - wing);
  const bpsLegs = [mkLeg('SELL', 'PE', bpsSell), mkLeg('BUY', 'PE', bpsBuy)];
  const bpsMetrics = evaluateStrategyMetrics(bpsLegs, lotSize);
  allStrategies.push({
    id: 'bull_put_spread', name: 'Bull Put Credit Spread', outlook: 'bullish', ivEnvironment: 'moderate', riskProfile: 'Defined Risk',
    rationale: "Bullish structure capturing put writer support wall. Profits if Nifty stays flat or ascends.",
    adjustmentRule: "If Nifty falls below sold put, convert to Iron Fly or close for 2x credit loss.",
    legs: bpsLegs, probabilityOfProfit: 68, ...bpsMetrics
  });

  // 4. Bull Call Spread
  const bcsBuy = customStrikes?.['bull_call_spread']?.[0] ?? atmStrike;
  const bcsSell = customStrikes?.['bull_call_spread']?.[1] ?? (bcsBuy + wing);
  const bcsLegs = [mkLeg('BUY', 'CE', bcsBuy), mkLeg('SELL', 'CE', bcsSell)];
  const bcsMetrics = evaluateStrategyMetrics(bcsLegs, lotSize);
  allStrategies.push({
    id: 'bull_call_spread', name: 'Bull Call Debit Spread', outlook: 'bullish', ivEnvironment: 'low', riskProfile: 'Defined Risk',
    rationale: "Low IV bullish momentum trade. Sold call reduces net cost and neutralizes vega drag.",
    adjustmentRule: "Close at 50% max profit target or 7 DTE.",
    legs: bcsLegs, probabilityOfProfit: 54, ...bcsMetrics
  });

  // 5. Bear Call Spread
  const bcs2Sell = customStrikes?.['bear_call_spread']?.[0] ?? getNearestStrike(spot, +1);
  const bcs2Buy = customStrikes?.['bear_call_spread']?.[1] ?? (bcs2Sell + wing);
  const bcs2Legs = [mkLeg('SELL', 'CE', bcs2Sell), mkLeg('BUY', 'CE', bcs2Buy)];
  const bcs2Metrics = evaluateStrategyMetrics(bcs2Legs, lotSize);
  allStrategies.push({
    id: 'bear_call_spread', name: 'Bear Call Credit Spread', outlook: 'bearish', ivEnvironment: 'moderate', riskProfile: 'Defined Risk',
    rationale: "Monetizes heavy call OI ceiling. Profits if Nifty drifts lower or stays below resistance.",
    adjustmentRule: "Roll down call spread if Nifty breaks support.",
    legs: bcs2Legs, probabilityOfProfit: 67, ...bcs2Metrics
  });

  // 6. Bear Put Spread
  const bps2Buy = customStrikes?.['bear_put_spread']?.[0] ?? atmStrike;
  const bps2Sell = customStrikes?.['bear_put_spread']?.[1] ?? (bps2Buy - wing);
  const bps2Legs = [mkLeg('BUY', 'PE', bps2Buy), mkLeg('SELL', 'PE', bps2Sell)];
  const bps2Metrics = evaluateStrategyMetrics(bps2Legs, lotSize);
  allStrategies.push({
    id: 'bear_put_spread', name: 'Bear Put Debit Spread', outlook: 'bearish', ivEnvironment: 'low', riskProfile: 'Defined Risk',
    rationale: "Sharp downside breakdown play. Limited risk with attractive 2:1 risk-reward profile.",
    adjustmentRule: "Take profit near major put wall support.",
    legs: bps2Legs, probabilityOfProfit: 52, ...bps2Metrics
  });

  // 7. Long Straddle
  const lsdBuyCE = customStrikes?.['long_straddle']?.[0] ?? atmStrike;
  const lsdBuyPE = customStrikes?.['long_straddle']?.[1] ?? atmStrike;
  const lsdLegs = [mkLeg('BUY', 'CE', lsdBuyCE), mkLeg('BUY', 'PE', lsdBuyPE)];
  const lsdMetrics = evaluateStrategyMetrics(lsdLegs, lotSize);
  allStrategies.push({
    id: 'long_straddle', name: 'Long Straddle (Vol Expansion)', outlook: 'volatile', ivEnvironment: 'low', riskProfile: 'Defined Risk',
    rationale: "Pure long gamma & vega explosion setup prior to major event catalyst (RBI/Fed/Budget).",
    adjustmentRule: "Scalp gamma by trimming profitable leg on 100pt directional spikes.",
    legs: lsdLegs, probabilityOfProfit: 44, ...lsdMetrics
  });

  // 8. Iron Butterfly
  const ibBuyPE = customStrikes?.['iron_butterfly']?.[0] ?? (atmStrike - wing);
  const ibSellPE = customStrikes?.['iron_butterfly']?.[1] ?? atmStrike;
  const ibSellCE = customStrikes?.['iron_butterfly']?.[2] ?? atmStrike;
  const ibBuyCE = customStrikes?.['iron_butterfly']?.[3] ?? (atmStrike + wing);
  const ibLegs = [mkLeg('BUY', 'PE', ibBuyPE), mkLeg('SELL', 'PE', ibSellPE), mkLeg('SELL', 'CE', ibSellCE), mkLeg('BUY', 'CE', ibBuyCE)];
  const ibMetrics = evaluateStrategyMetrics(ibLegs, lotSize);
  allStrategies.push({
    id: 'iron_butterfly', name: 'Iron Butterfly (Pinning Play)', outlook: 'neutral', ivEnvironment: 'high', riskProfile: 'Defined Risk',
    rationale: "Aggressive max pain pinning trade for expiry day. Collects maximum ATM credit.",
    adjustmentRule: "Close position before 2 PM on expiry day to avoid gamma assignment risk.",
    legs: ibLegs, probabilityOfProfit: 62, ...ibMetrics
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

export function calculatePayoffCurve(legs: OptionLeg[], spot: number, lotSize: number = CONFIG.lot_size): PayoffPoint[] {
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
        netPnl += (intrinsic - leg.premium) * leg.qtyRatio * lotSize;
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
