#!/usr/bin/env python3
"""persistence -- when does a bull put spread's theta stop paying for its downside delta?

The question, stated precisely: for a BULLISH structure, is the useful entry filter the
SIZE of the recent move (which is what every earlier test used) or its PERSISTENCE -- a
run of down days, a negative 3-day drift, distance below the 5DMA? Those are different
things: -0.8%, -0.5%, +1.0%, +0.7% is noise; four consecutive falls is a trend.

Structure: sell PE at spot-0.83%, buy PE at spot-1.67% (200 / 400 points at 24,000),
held 6 sessions, settled at intrinsic. Priced with Black-Scholes on the smile measured
from 20,990 of the user's own traded option prices, scaled by each day's India VIX.

Tested on 2018-2026 because a bearish-regime filter cannot be evaluated on a window that
contains no bearish regime -- the captured chain covers six calm weeks.
"""
import sqlite3, math
import numpy as np, pandas as pd

H, SO, LO, SLIP = 6, 0.833, 1.667, 0.0041
SM_X = np.array([-2.30,-1.75,-1.25,-0.75,-0.275,0.275,0.75,1.25,1.75,2.30])
SM_Y = np.array([14.89,14.09,13.47,13.00,12.60,12.91,12.73,12.56,12.64,12.89])/12.75
def smile(m): return float(np.interp(m, SM_X, SM_Y, left=SM_Y[0], right=SM_Y[-1]))
def _cdf(x): return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))
def bsput(S,K,T,s):
    if T<=0 or s<=0: return max(K-S,0.0)
    d1=(math.log(S/K)+0.5*s*s*T)/(s*math.sqrt(T)); d2=d1-s*math.sqrt(T)
    return K*_cdf(-d2)-S*_cdf(-d1)

from db_config import DB_PATH   # was a bare relative path — bound to the CWD (D-SC-06)
con=sqlite3.connect(DB_PATH)
o=pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN "
              "('NIFTY','INDIAVIX')",con)
o["d"]=pd.to_datetime(o.ts.str[:10])
O=o.pivot_table(index="d",columns="symbol",values="close").sort_index().dropna()
O["r"]=O.NIFTY.pct_change()*100
O["r3"]=O.NIFTY.pct_change(3)*100
O["r5"]=O.NIFTY.pct_change(5)*100
O["neg5"]=(O.r<0).rolling(5).sum()
O["d5ma"]=(O.NIFTY/O.NIFTY.rolling(5).mean()-1)*100
O["fwd"]=O.NIFTY.shift(-H)/O.NIFTY-1
D=O.dropna().copy()
T=H/252.0
pnl=[]
for vix,f in zip(D.INDIAVIX.values,D.fwd.values):
    S=100.0; sk,lk=S*(1-SO/100),S*(1-LO/100)
    cr=bsput(S,sk,T,vix/100*smile(-SO))-bsput(S,lk,T,vix/100*smile(-LO))-2*SLIP
    ST=S*(1+f)
    pnl.append(cr-(max(sk-ST,0)-max(lk-ST,0)))
D["pnl"]=pnl
width=(1-SO/100)-(1-LO/100)
print("bull put spread, %d entries %s..%s"%(len(D),D.index.min().date(),D.index.max().date()))
print("  median credit %.3f%% of spot   width %.3f%%   max loss %.3f%%"
      %(np.median([bsput(100,100*(1-SO/100),T,v/100*smile(-SO))-bsput(100,100*(1-LO/100),T,v/100*smile(-LO)) for v in D.INDIAVIX]),
        width*100, width*100-np.median([bsput(100,100*(1-SO/100),T,v/100*smile(-SO))-bsput(100,100*(1-LO/100),T,v/100*smile(-LO)) for v in D.INDIAVIX])))
print("  UNCONDITIONAL: mean %+.4f%%  win %.0f%%  worst %+.3f%%"
      %(D.pnl.mean(),(D.pnl>0).mean()*100,D.pnl.min()))

def blk(name,groups):
    print("\n=== %s ==="%name)
    print("   %-26s %6s %10s %8s %10s"%("bucket","n","mean P/L","win","worst"))
    print("   "+"-"*64)
    for lab,m in groups:
        s=D[m]
        if len(s)<60: continue
        print("   %-26s %6d %+10.4f %7.0f%% %+10.3f"
              %(lab,len(s),s.pnl.mean(),(s.pnl>0).mean()*100,s.pnl.min()))

blk("MAGNITUDE of the last move (what earlier tests used)",
    [("prior day > +1%%",D.r>1),("prior day -0.5..+0.5%%",D.r.abs()<0.5),
     ("prior day -1..-0.5%%",(D.r<=-0.5)&(D.r>-1)),("prior day < -1%%",D.r<=-1),
     ("prior day < -2%%",D.r<=-2)])

blk("PERSISTENCE: 3-day drift",
    [("R3 > +2%%",D.r3>2),("R3 0..+2%%",(D.r3>0)&(D.r3<=2)),
     ("R3 -2..0%%",(D.r3<=0)&(D.r3>-2)),("R3 -4..-2%%",(D.r3<=-2)&(D.r3>-4)),
     ("R3 < -4%%",D.r3<=-4)])

blk("PERSISTENCE: down days in last 5",
    [("0-1 down days",D.neg5<=1),("2 down days",D.neg5==2),("3 down days",D.neg5==3),
     ("4 down days",D.neg5==4),("5 down days",D.neg5==5)])

blk("POSITION vs 5-day moving average",
    [("above 5DMA by >1%%",D.d5ma>1),("above 5DMA 0..1%%",(D.d5ma>0)&(D.d5ma<=1)),
     ("below 5DMA 0..1%%",(D.d5ma<=0)&(D.d5ma>-1)),("below 5DMA >1%%",D.d5ma<=-1)])

print("\n=== WHICH CONDITIONER DISCRIMINATES MOST? (spread between best and worst bucket) ===")
for nm,col,bks in (("last-day move","r",[(-99,-2),(-2,-1),(-1,-0.5),(-0.5,0.5),(0.5,99)]),
                   ("3-day drift","r3",[(-99,-4),(-4,-2),(-2,0),(0,2),(2,99)]),
                   ("5-day drift","r5",[(-99,-5),(-5,-2),(-2,0),(0,3),(3,99)]),
                   ("down days in 5","neg5",[(-1,1),(1,2),(2,3),(3,4),(4,5)]),
                   ("dist from 5DMA","d5ma",[(-99,-1),(-1,0),(0,1),(1,99)])):
    ms=[D[(D[col]>a)&(D[col]<=b)].pnl.mean() for a,b in bks if ((D[col]>a)&(D[col]<=b)).sum()>=60]
    if len(ms)<3: continue
    print("   %-18s best %+.4f  worst %+.4f  spread %.4f"%(nm,max(ms),min(ms),max(ms)-min(ms)))
