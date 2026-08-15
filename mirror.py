#!/usr/bin/env python3
"""mirror -- (A) null-test the persistence buckets, (B) bull put vs bear call, same windows.

(A) owed from last turn: five conditioners x five buckets is 25 comparisons, so some
best-minus-worst spread is expected from noise. Scored by circularly shifting the
conditioner against the P&L, which preserves both series' own structure and destroys only
their alignment.

(B) the mirror test. The bear call spread is the structural opposite of the bull put --
but NOT its economic equal, because the smile measured from 20,990 of the user's own
traded prices is asymmetric: put IV runs 13.0-14.9% while call IV at equal distance runs
12.6-12.9%. You are paid less to sell calls than puts. Whether the bearish mirror
preserves the edge is therefore an open question, and this prices both structures on
every one of the same 2,093 windows.
"""
import sqlite3, math
import numpy as np, pandas as pd

H, OFF1, OFF2, SLIP = 6, 0.833, 1.667, 0.0041
RNG = np.random.default_rng(31415)
SM_X = np.array([-2.30,-1.75,-1.25,-0.75,-0.275,0.275,0.75,1.25,1.75,2.30])
SM_Y = np.array([14.89,14.09,13.47,13.00,12.60,12.91,12.73,12.56,12.64,12.89])/12.75
def smile(m): return float(np.interp(m,SM_X,SM_Y,left=SM_Y[0],right=SM_Y[-1]))
def _cdf(x): return 0.5*(1.0+math.erf(x/math.sqrt(2.0)))
def bs(S,K,T,s,call):
    if T<=0 or s<=0: return max(S-K,0.0) if call else max(K-S,0.0)
    d1=(math.log(S/K)+0.5*s*s*T)/(s*math.sqrt(T)); d2=d1-s*math.sqrt(T)
    return (S*_cdf(d1)-K*_cdf(d2)) if call else (K*_cdf(-d2)-S*_cdf(-d1))

con=sqlite3.connect("option_chains.db")
o=pd.read_sql("SELECT symbol,ts,close FROM price_bars WHERE timeframe='1d' AND symbol IN ('NIFTY','INDIAVIX')",con)
o["d"]=pd.to_datetime(o.ts.str[:10])
O=o.pivot_table(index="d",columns="symbol",values="close").sort_index().dropna()
O["r"]=O.NIFTY.pct_change()*100
O["r3"]=O.NIFTY.pct_change(3)*100
O["neg5"]=(O.r<0).rolling(5).sum()
O["fwd"]=O.NIFTY.shift(-H)/O.NIFTY-1
D=O.dropna().copy(); T=H/252.0
S=100.0
bp,bc,crp,crc=[],[],[],[]
for vix,f in zip(D.INDIAVIX.values,D.fwd.values):
    ST=S*(1+f)
    sk,lk=S*(1-OFF1/100),S*(1-OFF2/100)
    c=bs(S,sk,T,vix/100*smile(-OFF1),False)-bs(S,lk,T,vix/100*smile(-OFF2),False)
    crp.append(c); bp.append(c-2*SLIP-(max(sk-ST,0)-max(lk-ST,0)))
    ck,hk=S*(1+OFF1/100),S*(1+OFF2/100)
    c2=bs(S,ck,T,vix/100*smile(OFF1),True)-bs(S,hk,T,vix/100*smile(OFF2),True)
    crc.append(c2); bc.append(c2-2*SLIP-(max(ST-ck,0)-max(ST-hk,0)))
D["bullput"],D["bearcall"],D["cr_p"],D["cr_c"]=bp,bc,crp,crc

print("=== A. NULL TEST on the persistence buckets ===")
def spread(vals,cond,edges):
    ms=[vals[(cond>a)&(cond<=b)].mean() for a,b in edges if ((cond>a)&(cond<=b)).sum()>=60]
    return (max(ms)-min(ms)) if len(ms)>=3 else np.nan
tests=[("down days in 5","neg5",[(-1,1),(1,2),(2,3),(3,4),(4,5)]),
       ("3-day drift","r3",[(-99,-4),(-4,-2),(-2,0),(0,2),(2,99)]),
       ("last-day move","r",[(-99,-2),(-2,-1),(-1,-0.5),(-0.5,0.5),(0.5,99)])]
print("   %-18s %9s %11s %10s %8s"%("conditioner","observed","null median","null p95","p"))
print("   "+"-"*60)
n=len(D)
for nm,col,ed in tests:
    obs=spread(D.bullput.values,D[col].values,ed)
    nl=[]
    for _ in range(2000):
        sh=np.roll(D[col].values,int(RNG.integers(20,n-20)))
        v=spread(D.bullput.values,sh,ed)
        if v==v: nl.append(v)
    nl=np.array(nl); p=float((nl>=obs).mean())
    print("   %-18s %9.4f %11.4f %10.4f %8.3f%s"
          %(nm,obs,np.median(nl),np.percentile(nl,95),p,"  <<<" if p<0.05 else ""))

print("\n=== B. BULL PUT vs BEAR CALL, same 2,093 windows ===")
print("   credit collected:  put spread %.4f%%   call spread %.4f%%   put/call %.2fx"
      %(np.median(D.cr_p),np.median(D.cr_c),np.median(D.cr_p)/np.median(D.cr_c)))
print("   %-22s %10s %8s %10s %10s"%("structure","mean","win","worst","ES(5%)"))
print("   "+"-"*62)
for nm,col in (("BULL PUT (all days)","bullput"),("BEAR CALL (all days)","bearcall")):
    a=D[col].values; es=np.sort(a)[:int(len(a)*.05)].mean()
    print("   %-22s %+10.4f %7.0f%% %+10.3f %+10.4f"%(nm,a.mean(),(a>0).mean()*100,a.min(),es))

print("\n   each structure in the regime it is DESIGNED for:")
bull=(D.neg5<=2); bear=(D.neg5>=3)
print("   %-30s %6s %10s %8s"%("state / structure","n","mean","win"))
print("   "+"-"*58)
for nm,m,col in (("bullish (<=2 down) / bull put",bull,"bullput"),
                 ("bullish (<=2 down) / bear call",bull,"bearcall"),
                 ("bearish (>=3 down) / bull put",bear,"bullput"),
                 ("bearish (>=3 down) / bear call",bear,"bearcall")):
    s=D[m][col]
    print("   %-30s %6d %+10.4f %7.0f%%"%(nm,len(s),s.mean(),(s>0).mean()*100))
sw=np.where(bull,D.bullput,D.bearcall)
print("\n   SWITCHING (bull put when <=2 down, bear call when >=3): mean %+.4f  win %.0f%%"
      %(sw.mean(),(sw>0).mean()*100))
print("   always bull put: %+.4f   always bear call: %+.4f"%(D.bullput.mean(),D.bearcall.mean()))
