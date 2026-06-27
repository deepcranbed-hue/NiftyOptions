export interface OptionRow {
  call_oichg: number;
  call_oi: number;
  call_ltp: number;
  strike: number;
  iv: number;
  put_ltp: number;
  put_oi: number;
  put_oichg: number;
}

export type MarketTone = 'bull' | 'bear' | 'neutral' | 'caution';

export interface AutomatedRead {
  tone: MarketTone;
  text: string;
}

export interface StructureContextItem {
  label: string;
  text: string;
}

export interface ComplacencyMetrics {
  iv: number;
  comp_iv: number;
  accel: number;
  bursts: number;
  max_burst: number;
  score: number;
  verdict: {
    tone: MarketTone;
    msg: string;
  };
}

export interface GlobalCueItem {
  name: string;
  pct: number;
  sector: string;
  inverse: boolean;
  tone: MarketTone;
  arrow: 'tailwind' | 'headwind' | 'neutral';
  read: string;
}

export interface NewsHeadline {
  sector: string;
  tone: MarketTone;
  score: number;
  headline: string;
}

export interface OptionLeg {
  action: 'BUY' | 'SELL';
  type: 'CE' | 'PE';
  strike: number;
  premium: number;
  qtyRatio: number;
  delta?: number;
  theta?: number;
  vega?: number;
}

export interface StrategyRecommendation {
  id: string;
  name: string;
  outlook: 'bullish' | 'bearish' | 'neutral' | 'volatile';
  ivEnvironment: 'low' | 'moderate' | 'high';
  riskProfile: 'Defined Risk' | 'Undefined Risk';
  netPremium: number; // + for debit, - for credit
  maxProfit: string;
  maxLoss: string;
  breakevens: number[];
  probabilityOfProfit: number;
  rationale: string;
  adjustmentRule: string;
  legs: OptionLeg[];
}

export interface PayoffPoint {
  price: number;
  pnl: number;
  isSpot?: boolean;
  isBreakeven?: boolean;
}

export interface RiskConfig {
  capital: number;
  risk_per_trade_pct: number;
  max_portfolio_heat_pct: number;
  max_net_delta_units: number;
  max_net_vega_rupees: number;
  max_drawdown_pct: number;
  lot_size: number;
  complacency_block: number;
  complacency_halve: number;
}
