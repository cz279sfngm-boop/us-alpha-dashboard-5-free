from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any, Iterable

import requests

from research import attach_research_reports, fetch_research_evidence

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
HIST = CACHE / "ohlcv"
HIST.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/141 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9"
}
SEC_HEADERS = {
    "User-Agent": "USMultiHorizonAlphaDashboard/5.0 research contact via GitHub repository",
    "Accept": "application/json,text/plain,*/*"
}

THEMES = {
    "AI Infrastructure": re.compile(r"semiconductor|chip|computer hardware|server|network|data center|electronic components", re.I),
    "Semiconductors": re.compile(r"semiconductor|chip|integrated circuit", re.I),
    "Data Center": re.compile(r"data center|server|networking|data storage", re.I),
    "Cybersecurity": re.compile(r"cyber|security software|information security", re.I),
    "Space": re.compile(r"space|satellite|aerospace", re.I),
    "Defense": re.compile(r"defen[cs]e|aerospace & defense|military", re.I),
    "Robotics & Automation": re.compile(r"robot|automation|industrial machinery|specialty industrial machinery", re.I),
    "Nuclear & Uranium": re.compile(r"uranium|nuclear", re.I),
    "Power Grid": re.compile(r"electric utilit|electrical equipment|power generation|transmission|grid", re.I),
    "Quantum Computing": re.compile(r"quantum", re.I),
    "GLP-1 / Obesity": re.compile(r"glp-1|obesity|weight loss", re.I),
}

def safe_num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None: return default
        if isinstance(v, dict): v = v.get("raw", v.get("fmt"))
        s = str(v).replace("$","").replace(",","").replace("%","").strip()
        if not s or s in {"N/A","--"}: return default
        x=float(s); return x if math.isfinite(x) else default
    except Exception: return default

def nullable(v: Any) -> float | None:
    try:
        if v is None: return None
        if isinstance(v, dict): v=v.get("raw",v.get("fmt"))
        x=float(v); return x if math.isfinite(x) else None
    except Exception: return None

def mean(xs: Iterable[float]) -> float:
    xs=list(xs); return sum(xs)/len(xs) if xs else 0.0

def ema_series(xs:list[float|None],n:int)->list[float|None]:
    out=[]; a=2/(n+1); prev=None
    for x in xs:
        if x is None: out.append(None); continue
        prev=x if prev is None else a*x+(1-a)*prev
        out.append(prev)
    return out

def ema(xs:list[float],n:int)->float:
    return float(ema_series([float(x) for x in xs],n)[-1]) if xs else 0.0

def sma_series(xs:list[float|None],n:int)->list[float|None]:
    out=[None]*len(xs)
    for i in range(n-1,len(xs)):
        w=xs[i-n+1:i+1]
        if any(x is None for x in w): continue
        out[i]=mean(float(x) for x in w if x is not None)
    return out

def rsi(xs:list[float],n:int=14)->float:
    if len(xs)<n+1:return 50.0
    gains=losses=0.0
    for i in range(len(xs)-n,len(xs)):
        d=xs[i]-xs[i-1]; gains+=max(d,0); losses+=max(-d,0)
    ag,al=gains/n,losses/n
    return 100.0 if al==0 else 100-100/(1+ag/al)

def atr(h:list[float],l:list[float],c:list[float],n:int=14)->float|None:
    if len(c)<n+1:return None
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return mean(trs[-n:])

def drawdown(xs:list[float])->float:
    peak=-math.inf; worst=0.0
    for x in xs:
        peak=max(peak,x)
        if peak>0: worst=min(worst,(x/peak-1)*100)
    return worst

def percentile(vals:list[float],x:float)->float:
    if not vals:return 50.0
    less=sum(v<x for v in vals); eq=sum(v==x for v in vals)
    return (less+0.5*eq)/len(vals)*100

def clamp(x:float,lo=0,hi=100)->int:
    return int(round(max(lo,min(hi,x))))

def themes_for(name:str,sector:str,industry:str)->list[str]:
    text=f"{name} {sector} {industry}"
    return [k for k,p in THEMES.items() if p.search(text)]

def http_json(url:str,retries=3,timeout=35)->Any:
    last=None
    headers=SEC_HEADERS if "sec.gov" in url else HEADERS
    for attempt in range(retries):
        try:
            r=requests.get(url,headers=headers,timeout=timeout)
            if r.status_code==429:
                time.sleep(1.5*(attempt+1)); continue
            r.raise_for_status(); return r.json()
        except Exception as e:
            last=e; time.sleep(0.5*(attempt+1))
    raise RuntimeError(f"request failed: {url} :: {last}")

def fetch_universe()->tuple[list[dict],dict]:
    j=http_json("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&offset=0&download=true")
    rows=j.get("data",{}).get("rows",[]) or []; eligible=[]; excluded=0
    for x in rows:
        symbol=str(x.get("symbol","")).strip().upper(); name=str(x.get("name",""))
        sector=str(x.get("sector","") or "").strip(); industry=str(x.get("industry","") or "").strip()
        price=safe_num(x.get("lastsale")); volume=safe_num(x.get("volume")); market_cap=safe_num(x.get("marketCap"))
        bad=re.search(r"depositary|\badr\b|etf|exchange traded|etn|fund|warrant|preferred|rights?\b|acquisition corp|\bunit\b",name,re.I)
        if not re.match(r"^[A-Z][A-Z.-]{0,8}$",symbol) or price<1 or volume<=0 or market_cap<=0 or bad:
            excluded+=1; continue
        eligible.append({"symbol":symbol,"name":name,"exchange":str(x.get("exchange","") or ""),"sector":sector,"industry":industry,"themes":themes_for(name,sector,industry),"price":price,"changePct":safe_num(x.get("pctchange")),"volume":volume,"marketCap":market_cap,"dailyDollarVolume":price*volume})
    return eligible,{"totalDiscovered":len(rows),"excludedSecurities":excluded,"eligibleCommonStocks":len(eligible)}

def yahoo_url(symbol:str,range_:str)->str:
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}?range={range_}&interval=1d&includePrePost=true&events=div%2Csplits"

def fetch_yahoo_bars(symbol:str,range_:str)->dict:
    j=http_json(yahoo_url(symbol,range_),retries=1,timeout=15); z=(j.get("chart",{}).get("result") or [None])[0]
    if not z: raise RuntimeError("Yahoo: no chart")
    q=((z.get("indicators") or {}).get("quote") or [{}])[0]
    adj=(((z.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []
    ts=z.get("timestamp") or []; bars=[]
    for i,t in enumerate(ts):
        try:
            o,h,l,c,v=(float(q[k][i]) for k in ("open","high","low","close","volume"))
            ac=float(adj[i]) if i<len(adj) and adj[i] is not None else c
            bars.append({"date":dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat(),"open":o,"high":h,"low":l,"close":c,"adjClose":ac,"volume":v})
        except Exception: pass
    meta=z.get("meta") or {}; mt=meta.get("regularMarketTime")
    return {"bars":bars,"marketDataAt":dt.datetime.fromtimestamp(mt,dt.timezone.utc).isoformat() if mt else None,"regularMarketPrice":nullable(meta.get("regularMarketPrice")),"splits":len((z.get("events") or {}).get("splits") or {}),"dividends":len((z.get("events") or {}).get("dividends") or {}),"provider":"Yahoo","adjustment":"adjusted-close"}

def _nasdaq_number(v:Any)->float:
    if v is None: raise ValueError("missing")
    s=str(v).replace("$","").replace(",","").strip()
    if not s or s in {"N/A","--"}: raise ValueError("missing")
    return float(s)

def fetch_nasdaq_bars(symbol:str,range_:str)->dict:
    today=dt.datetime.now(dt.timezone.utc).date()
    lookback=18 if range_=="10d" else 560
    start=today-dt.timedelta(days=lookback)
    params={
        "assetclass":"stocks",
        "fromdate":start.isoformat(),
        "todate":today.isoformat(),
        "limit":"5000"
    }
    url=f"https://api.nasdaq.com/api/quote/{requests.utils.quote(symbol)}/historical?"+requests.compat.urlencode(params)
    j=http_json(url,retries=3,timeout=35)
    rows=((((j.get("data") or {}).get("tradesTable") or {}).get("rows")) or [])
    bars=[]
    for row in rows:
        try:
            ds=str(row.get("date") or "").strip()
            d=dt.datetime.strptime(ds,"%m/%d/%Y").date().isoformat()
            o=_nasdaq_number(row.get("open")); h=_nasdaq_number(row.get("high")); l=_nasdaq_number(row.get("low"))
            close=_nasdaq_number(row.get("close") if row.get("close") is not None else row.get("close/last"))
            vol=_nasdaq_number(row.get("volume"))
            if min(o,h,l,close)<=0: continue
            bars.append({"date":d,"open":o,"high":h,"low":l,"close":close,"adjClose":close,"volume":vol})
        except Exception:
            continue
    bars.sort(key=lambda x:x["date"])
    if not bars: raise RuntimeError("Nasdaq historical: no bars")
    return {"bars":bars,"marketDataAt":dt.datetime.now(dt.timezone.utc).isoformat(),"regularMarketPrice":bars[-1]["close"],"splits":0,"dividends":0,"provider":"Nasdaq Public Historical","adjustment":"raw-unadjusted"}

def fetch_bars(symbol:str,range_:str)->dict:
    preferred=os.getenv("ALPHA_HISTORY_PROVIDER","nasdaq").lower()
    if preferred=="yahoo":
        try:
            return fetch_yahoo_bars(symbol,range_)
        except Exception:
            return fetch_nasdaq_bars(symbol,range_)
    try:
        return fetch_nasdaq_bars(symbol,range_)
    except Exception as nasdaq_err:
        try:
            return fetch_yahoo_bars(symbol,range_)
        except Exception as yahoo_err:
            raise RuntimeError(f"Nasdaq={nasdaq_err}; Yahoo={yahoo_err}")

def hist_path(symbol:str)->Path:
    return HIST/f"{symbol.replace('/','_')}.json"

def load_history(symbol:str,force=False)->dict:
    path=hist_path(symbol); cached=None
    if path.exists():
        try: cached=json.loads(path.read_text())
        except Exception: pass
    fresh=False
    if cached and cached.get("refreshedAt"):
        try:
            t=dt.datetime.fromisoformat(cached["refreshedAt"].replace("Z","+00:00"))
            fresh=dt.datetime.now(dt.timezone.utc)-t<dt.timedelta(minutes=45)
        except Exception: pass
    if cached and len(cached.get("bars",[]))>=205 and fresh and not force:return cached
    if not cached or len(cached.get("bars",[]))<205:
        d=fetch_bars(symbol,"1y")
        if len(d["bars"])<205:raise RuntimeError("short history")
        out={"symbol":symbol,"bars":d["bars"][-370:],"marketDataAt":d["marketDataAt"],"refreshedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"splits":d["splits"],"dividends":d["dividends"],"mode":"bootstrap","provider":d.get("provider"),"adjustment":d.get("adjustment")}
    else:
        d=fetch_bars(symbol,"10d")
        if d["splits"]:
            d=fetch_bars(symbol,"1y"); bars=d["bars"][-370:]; mode="split-rebuild"
        else:
            merged={b["date"]:b for b in cached["bars"]}; merged.update({b["date"]:b for b in d["bars"]})
            bars=[merged[k] for k in sorted(merged)][-370:]; mode="incremental"
        out={"symbol":symbol,"bars":bars,"marketDataAt":d["marketDataAt"] or cached.get("marketDataAt"),"refreshedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"splits":max(cached.get("splits",0),d["splits"]),"dividends":max(cached.get("dividends",0),d["dividends"]),"mode":mode,"provider":d.get("provider") or cached.get("provider"),"adjustment":d.get("adjustment") or cached.get("adjustment")}
    path.write_text(json.dumps(out,separators=(",",":"))); return out

def series_from_cache(cache:dict)->dict:
    c=[];h=[];l=[];v=[];dates=[];rawc=[]
    for b in cache["bars"]:
        close=float(b["close"]); adj=float(b.get("adjClose",close)); factor=adj/close if close else 1
        c.append(adj); h.append(float(b["high"])*factor); l.append(float(b["low"])*factor); v.append(float(b["volume"])); rawc.append(close); dates.append(b["date"])
    return {"c":c,"h":h,"l":l,"v":v,"dates":dates,"rawClose":rawc,"lastPrice":rawc[-1],"marketDataAt":cache.get("marketDataAt"),"cacheMode":cache.get("mode"),"historyProvider":cache.get("provider"),"historyAdjustment":cache.get("adjustment")}


def wavetrend(h:list[float],l:list[float],c:list[float])->dict:
    src=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    esa=ema_series(src,9)
    dev=[abs(src[i]-esa[i]) if esa[i] is not None else None for i in range(len(src))]
    de=ema_series(dev,9)
    ci=[(src[i]-esa[i])/(0.015*de[i]) if esa[i] is not None and de[i] not in (None,0) else None for i in range(len(src))]
    w1=ema_series(ci,12); w2=sma_series(w1,3); last=len(c)-1
    wt1=w1[last]; wt2=w2[last]; vwap=(wt1-wt2) if wt1 is not None and wt2 is not None else None
    cross="None"; bars_since=None
    for i in range(last,0,-1):
        vals=(w1[i],w2[i],w1[i-1],w2[i-1])
        if any(x is None for x in vals): continue
        bull=w1[i]>w2[i] and w1[i-1]<=w2[i-1]
        bear=w1[i]<w2[i] and w1[i-1]>=w2[i-1]
        if bull or bear:
            cross="Bullish Cross" if bull else "Bearish Cross"; bars_since=last-i; break
    score=50
    if wt1 is not None and wt2 is not None: score+=10 if wt1>wt2 else -10
    if vwap is not None: score+=6 if vwap>0 else -6 if vwap<0 else 0
    if last>0 and wt1 is not None and w1[last-1] is not None: score+=8 if wt1>w1[last-1] else -8
    if bars_since is not None and bars_since<=5:
        impact=16*(1-bars_since/6); score+=impact if cross=="Bullish Cross" else -impact
    oversold=wt2 is not None and wt2<=-53; overbought=wt2 is not None and wt2>=53
    if oversold and wt1 is not None and wt2 is not None and wt1>wt2:score+=10
    if overbought and wt1 is not None and wt2 is not None and wt1<wt2:score-=10
    state="資料不足" if wt1 is None or wt2 is None else ("Recent Bullish Cross" if cross=="Bullish Cross" and (bars_since if bars_since is not None else 99)<=3 else "Recent Bearish Cross" if cross=="Bearish Cross" and (bars_since if bars_since is not None else 99)<=3 else "Bullish WT Structure" if wt1>wt2 else "Bearish WT Structure")
    return {"wt1":wt1,"wt2":wt2,"wtVwap":vwap,"wtCross":cross,"wtBarsSinceCross":bars_since,"wtOversold":oversold,"wtOverbought":overbought,"waveTrendScore":clamp(score,5,95),"waveTrendState":state}

def pivot_low(vals:list[float],i:int,left=5,right=5)->bool:
    p=vals[i]; return all(vals[j]>p for j in range(i-left,i+right+1) if j!=i)

def pivot_high(vals:list[float],i:int,left=5,right=5)->bool:
    p=vals[i]; return all(vals[j]<p for j in range(i-left,i+right+1) if j!=i)

def market_cipher_mf(h:list[float],l:list[float],c:list[float],dates:list[str],left=5,right=5,maxdist=1000)->dict:
    hlc=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    m=sma_series(hlc,5)
    dev=[abs(hlc[i]-m[i]) if m[i] is not None else None for i in range(len(c))]
    ff=sma_series(dev,5)
    intensity=[(hlc[i]-m[i])/(0.015*ff[i]) if m[i] is not None and ff[i] not in (None,0) else None for i in range(len(c))]
    mf=sma_series(intensity,60)
    lows=[]; highs=[]; lastdiv=None
    for i in range(left,len(c)-right):
        if mf[i] is None: continue
        if pivot_low(l,i,left,right):
            cur=(i,l[i],mf[i]); prev=lows[-1] if lows else None; lows.append(cur)
            if prev and i-prev[0]<=maxdist and cur[1]<prev[1] and cur[2]>prev[2]: lastdiv=("Bullish",i+right,i)
        if pivot_high(h,i,left,right):
            cur=(i,h[i],mf[i]); prev=highs[-1] if highs else None; highs.append(cur)
            if prev and i-prev[0]<=maxdist and cur[1]>prev[1] and cur[2]<prev[2]: lastdiv=("Bearish",i+right,i)
    last=len(c)-1; value=mf[last]; prev=mf[last-1] if last>0 else None
    slope=(value-prev) if value is not None and prev is not None else None
    barsago=last-lastdiv[1] if lastdiv and lastdiv[1]<=last else None
    div=lastdiv[0] if lastdiv and barsago is not None and barsago<=20 else "None"
    score=50
    if value is not None: score+=max(-10,min(10,value*0.18))
    if slope is not None: score+=9 if slope>0 else -9 if slope<0 else 0
    if div!="None" and barsago is not None:
        impact=28*(1-barsago/21); score+=impact if div=="Bullish" else -impact
    state="資料不足" if value is None else "Confirmed Bullish MF Divergence" if div=="Bullish" else "Confirmed Bearish MF Divergence" if div=="Bearish" else "Positive & Rising MF" if value>0 and (slope or 0)>0 else "Negative & Falling MF" if value<0 and (slope or 0)<0 else "Positive MF" if value>=0 else "Negative MF"
    return {"mfValue":value,"mfSlope":slope,"mfDivergence":div,"mfDivBarsAgo":barsago,"mfDivDate":dates[lastdiv[2]] if lastdiv else None,"mfScore":clamp(score,5,95),"mfState":state}

def technical(stock:dict,hist:dict,spy_ret20:float)->dict:
    c,h,l,v=hist["c"],hist["h"],hist["l"],hist["v"]; last=c[-1]
    e20,e50,e200=ema(c[-100:],20),ema(c[-160:],50),ema(c,200)
    av20=mean(v[-20:]); rv=v[-1]/av20 if av20 else 0
    ret20=(last/c[-21]-1)*100; rs20=ret20-spy_ret20
    rr=rsi(c,14); atr14=atr(h,l,c,14)
    high52=max(h[-252:]); low52=min(l[-252:]); dist52=(last/high52-1)*100 if high52 else 0
    def ret(days): return (last/c[-1-days]-1)*100 if len(c)>days else 0
    dr=[(c[i]/c[i-1]-1)*100 for i in range(max(1,len(c)-60),len(c))]
    vol20=statistics.pstdev(dr[-20:])*math.sqrt(252) if len(dr)>=2 else 0
    prior20=max(h[-21:-1]); breakout=last>prior20 and rv>=1.5
    vol60=statistics.pstdev(dr) if len(dr)>=2 else 0; vol20raw=statistics.pstdev(dr[-20:]) if len(dr)>=2 else 0
    vcp=vol60>0 and vol20raw<vol60*0.72 and rv<0.9 and last>e20
    wt=wavetrend(h,l,c); mf=market_cipher_mf(h,l,c,hist["dates"])
    ind=clamp(wt["waveTrendScore"]*0.55+mf["mfScore"]*0.45)
    volume_state="Healthy Breakout" if breakout else "Speculative Spike" if rv>=2.3 else "VCP Contraction" if vcp else "Institutional Accumulation" if rv>=1.4 and last>e20 else "Distribution" if rv>=1.5 and last<e20 else "Normal"
    return {"ema20":e20,"ema50":e50,"ema200":e200,"rsi14":rr,"rvol":rv,"rs20":rs20,"macd":ema(c,12)-ema(c,26),"atr14":atr14,"high52":high52,"low52":low52,"distance52High":dist52,"return1m":ret(21),"return3m":ret(63),"return6m":ret(126),"return1y":ret(min(251,len(c)-1)),"volatility20":vol20,"maxDrawdown1y":drawdown(c[-252:]),"avgVolume20":av20,"avgDollarTurnover20":mean([c[-20+i]*v[-20+i] for i in range(20)]),"breakout":breakout,"vcp":vcp,"volumeState":volume_state,"marketDataAt":hist.get("marketDataAt"),"indicatorScore":ind,**wt,**mf}

def sec_ticker_map()->dict[str,str]:
    j=http_json("https://www.sec.gov/files/company_tickers.json")
    return {str(x.get("ticker","")).upper():str(x.get("cik_str","")).zfill(10) for x in j.values() if x.get("ticker")}

def latest_annual(facts:dict,names:list[str],unit:str)->list[dict]:
    for name in names:
        arr=facts.get("us-gaap",{}).get(name,{}).get("units",{}).get(unit)
        if not isinstance(arr,list): continue
        by={}
        for x in arr:
            if x.get("form")!="10-K": continue
            fy=x.get("fy"); val=x.get("val")
            if fy is None or val is None: continue
            prev=by.get(int(fy))
            if not prev or str(x.get("filed",""))>str(prev.get("filed","")): by[int(fy)]=x
        vals=[by[k] for k in sorted(by)]
        if vals:return vals
    return []

def sec_fundamentals(symbol:str,cikmap:dict[str,str])->dict:
    empty={"fundamentalConfidence":"低","revenueGrowth":None,"epsGrowth":None,"fcfMargin":None,"roe":None,"roic":None,"operatingMargin":None,"grossMargin":None,"netMargin":None,"cash":None,"debt":None,"latestFiled":None}
    cik=cikmap.get(symbol)
    if not cik:return empty
    try:j=http_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",retries=2,timeout=20)
    except Exception:return empty
    facts=j.get("facts",{})
    rev=latest_annual(facts,["RevenueFromContractWithCustomerExcludingAssessedTax","Revenues","SalesRevenueNet"],"USD")
    eps=latest_annual(facts,["EarningsPerShareDiluted","EarningsPerShareBasic"],"USD/shares")
    ni=latest_annual(facts,["NetIncomeLoss","ProfitLoss"],"USD")
    ocf=latest_annual(facts,["NetCashProvidedByUsedInOperatingActivities"],"USD")
    capex=latest_annual(facts,["PaymentsToAcquirePropertyPlantAndEquipment"],"USD")
    eq=latest_annual(facts,["StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],"USD")
    op=latest_annual(facts,["OperatingIncomeLoss"],"USD")
    gp=latest_annual(facts,["GrossProfit"],"USD")
    cashf=latest_annual(facts,["CashAndCashEquivalentsAtCarryingValue"],"USD")
    debtf=latest_annual(facts,["LongTermDebtAndFinanceLeaseObligations","LongTermDebtNoncurrent","LongTermDebt"],"USD")
    def lv(a):return float(a[-1]["val"]) if a else None
    def gr(a):
        if len(a)<2:return None
        a0,a1=float(a[-2]["val"]),float(a[-1]["val"])
        return ((a1/a0)-1)*100 if a0>0 and a1>=0 else None
    r=lv(rev); nii=lv(ni); eqv=lv(eq); oc=lv(ocf); cx=lv(capex); opv=lv(op); cash=lv(cashf); debt=lv(debtf)
    fcf=(oc-abs(cx or 0)) if oc is not None else None
    roe=nii/eqv*100 if nii is not None and eqv not in (None,0) else None
    roic=None
    if opv is not None and eqv is not None and debt is not None and cash is not None and eqv+debt-cash>0:
        roic=opv*0.79/(eqv+debt-cash)*100
    opm=opv/r*100 if opv is not None and r not in (None,0) else None
    gpm=lv(gp)/r*100 if lv(gp) is not None and r not in (None,0) else None
    nm=nii/r*100 if nii is not None and r not in (None,0) else None
    fcfm=fcf/r*100 if fcf is not None and r not in (None,0) else None
    vals=[gr(rev),gr(eps),fcfm,roe,opm,roic]; cnt=sum(v is not None for v in vals)
    filed=max([x[-1].get("filed","") for x in (rev,eps,ni,op) if x],default=None)
    return {"fundamentalConfidence":"高" if cnt>=5 else "中" if cnt>=3 else "低","revenueGrowth":gr(rev),"epsGrowth":gr(eps),"fcfMargin":fcfm,"roe":roe,"roic":roic,"operatingMargin":opm,"grossMargin":gpm,"netMargin":nm,"cash":cash,"debt":debt,"latestFiled":filed}

def score_candidate(stock:dict,t:dict,f:dict,research:dict)->dict:
    rev=research.get("revisionScore"); catalyst=research.get("catalystScore"); valuation=None
    alpha=None if f["fundamentalConfidence"]=="低" else clamp(45+max(-12,min(18,(f["revenueGrowth"] or 0)*0.5))+max(-8,min(10,(f["fcfMargin"] or 0)*0.35)))
    tech_s=clamp(43+(10 if stock["price"]>t["ema20"] else 0)+(9 if t["ema20"]>t["ema50"] else 0)+(6 if t["ema50"]>t["ema200"] else 0)+(8 if t["breakout"] else 0)+(5 if t["vcp"] else 0)+max(-16,min(16,t["rs20"]*1.1))+max(-8,min(12,(t["rvol"]-1)*8)))
    tech_m=clamp(44+max(-10,min(12,t["return1m"]*0.4))+max(-10,min(14,t["return3m"]*0.25))+max(-8,min(10,t["return6m"]*0.12))+(8 if stock["price"]>t["ema50"] else 0)+(7 if t["ema50"]>t["ema200"] else 0))
    tech_g=clamp(48+(8 if stock["price"]>t["ema200"] else 0)+(7 if t["ema50"]>t["ema200"] else 0)+max(-9,min(10,t["rs20"]*0.55)))
    business=None if f["fundamentalConfidence"]=="低" else clamp(45+max(-14,min(18,(f["revenueGrowth"] or 0)*0.7))+max(-8,min(12,(f["operatingMargin"] or 0)*0.35)))
    growth=None if f["fundamentalConfidence"]=="低" else clamp(42+max(-16,min(20,(f["revenueGrowth"] or 0)*0.7))+max(-12,min(17,(f["epsGrowth"] or 0)*0.35))+max(-10,min(15,(f["fcfMargin"] or 0)*0.65))+max(-8,min(10,(f["roe"] or 0)*0.3))+max(-7,min(10,(f["roic"] or 0)*0.25)))
    risk=clamp(78+(6 if t["avgDollarTurnover20"]>=10_000_000 else 0 if t["avgDollarTurnover20"]>=3_000_000 else -14)-max(0,abs(t["maxDrawdown1y"])-30)*0.45)
    def wscore(parts):
        avail=[(v,w) for v,w in parts if v is not None]; tw=sum(w for _,w in avail)
        if not tw:return 20
        return clamp(sum(v*w for v,w in avail)/tw-(1-tw)*18)
    s=wscore([(alpha,.15),(rev,.2),(tech_s,.25),(catalyst,.15),(business,.05),(valuation,.1),(risk,.1)])
    m=wscore([(alpha,.15),(rev,.25),(tech_m,.25),(catalyst,.1),(business,.1),(valuation,.05),(risk,.1)])
    g=wscore([(alpha,.15),(rev,.1),(tech_g,.05),(catalyst,.05),(growth,.35),(valuation,.2),(risk,.1)])
    if f["fundamentalConfidence"]=="低":g=min(g,59);m=min(m,64)
    if t["avgDollarTurnover20"]<10_000_000 or stock["marketCap"]<500_000_000 or stock["price"]<5:s=min(s,69)
    if t["avgDollarTurnover20"]<3_000_000 or stock["marketCap"]<300_000_000:g=min(g,69)
    evidence_count=sum([f["fundamentalConfidence"]!="低",research.get("revisionConfidence")!="低",research.get("catalystConfidence")!="低"])
    data_conf="高" if evidence_count>=3 else "中" if evidence_count>=1 else "低"
    return {**stock,**t,**f,**research,"rawSScore":s,"rawMScore":m,"rawGScore":g,"sScore":s,"mScore":m,"gScore":g,"riskScore":risk,"trueAlphaScore":alpha or 0,"momentumScore":clamp(38+max(-15,min(20,t["rs20"]*1.15))+max(-9,min(17,(t["rvol"]-1)*10))+max(-7,min(8,t["return1m"]*0.25))),"hypeScore":0,"valuationAttractiveness":None,"epsRevision30d":None,"revenueRevision30d":None,"forwardPE":None,"pool":"","status":"FAILED","dataConfidence":data_conf,"classificationSource":"Nasdaq sector/industry + deterministic theme rules"}

def calibrate(rows:list[dict])->None:
    sv=[r["rawSScore"] for r in rows]; mv=[r["rawMScore"] for r in rows]; gv=[r["rawGScore"] for r in rows]
    for r in rows:
        r["sScore"]=min(97,clamp(r["rawSScore"]*.65+(20+percentile(sv,r["rawSScore"])*.75)*.35))
        r["mScore"]=min(97,clamp(r["rawMScore"]*.65+(20+percentile(mv,r["rawMScore"])*.75)*.35))
        r["gScore"]=min(97,clamp(r["rawGScore"]*.65+(20+percentile(gv,r["rawGScore"])*.75)*.35))
        if r["dataConfidence"]=="低":r["sScore"]=min(r["sScore"],84);r["mScore"]=min(r["mScore"],79);r["gScore"]=min(r["gScore"],69)
        r["dScore"]=min(96,clamp(min(r["sScore"],r["mScore"],r["gScore"])*.45+mean([r["sScore"],r["mScore"],r["gScore"]])*.2+r["trueAlphaScore"]*.25+50*.1))
        tags=[]
        if r["sScore"]>=70:tags.append("S")
        if r["mScore"]>=70:tags.append("M")
        if r["gScore"]>=70 and r["fundamentalConfidence"]!="低":tags.append("G")
        if "S" in tags and "M" in tags:tags.append("SM")
        if "M" in tags and "G" in tags:tags.append("MG")
        if r["sScore"]>=78 and r["mScore"]>=76 and r["gScore"]>=75 and r["fundamentalConfidence"]!="低" and (r.get("revisionConfidence")!="低" or r.get("catalystConfidence")!="低"):tags.append("SMG")
        r["pool"]=",".join(tags)
        evidence_ok=(r.get("revisionConfidence")!="低" or r.get("catalystConfidence")!="低")
        r["status"]="READY" if r["sScore"]>=84 and r["price"]>r["ema20"] and evidence_ok else "WATCH" if r["sScore"]>=72 else "EXTENDED" if r["sScore"]>=64 else "FAILED"

def seed_universe(eligible:list[dict],limit=120)->list[dict]:
    liquid=[x for x in eligible if x["dailyDollarVolume"]>=5_000_000 and x["marketCap"]>=300_000_000]
    m={}
    for x in sorted(liquid,key=lambda z:(z["changePct"]*2+math.log10(max(1,z["dailyDollarVolume"]))),reverse=True)[:140]:m[x["symbol"]]=x
    for x in sorted(liquid,key=lambda z:(math.log10(z["marketCap"])+math.log10(z["dailyDollarVolume"])*.45),reverse=True)[:100]:m[x["symbol"]]=x
    for x in sorted([z for z in liquid if z["marketCap"]<=20_000_000_000],key=lambda z:z["dailyDollarVolume"],reverse=True)[:80]:m[x["symbol"]]=x
    sectors={}
    for x in liquid:sectors.setdefault(x["sector"] or "Unclassified",[]).append(x)
    for arr in sectors.values():
        for x in sorted(arr,key=lambda z:z["dailyDollarVolume"],reverse=True)[:8]:m[x["symbol"]]=x
    return list(m.values())[:limit]

def scan(mode:str,output:Path,force=False)->None:
    started=dt.datetime.now(dt.timezone.utc)
    eligible,stats=fetch_universe(); seeds=seed_universe(eligible)
    spy_ret=0
    try:
        sh=series_from_cache(load_history("SPY",force=force)); spy_ret=(sh["c"][-1]/sh["c"][-21]-1)*100
    except Exception:pass
    results=[]; failures=[]
    def one(stock):
        cache=load_history(stock["symbol"],force=force); hist=series_from_cache(cache)
        return stock,technical(stock,hist,spy_ret)
    with cf.ThreadPoolExecutor(max_workers=int(os.getenv("ALPHA_WORKERS","10"))) as ex:
        futs={ex.submit(one,s):s for s in seeds}
        for fut in cf.as_completed(futs):
            s=futs[fut]
            try:results.append(fut.result())
            except Exception as e:
                msg=str(e)[:240]
                failures.append({"symbol":s["symbol"],"error":msg})
                if len(failures)<=12:
                    print(f"DEEP_FAIL {s['symbol']}: {msg}", flush=True)
    cikmap={}
    try:cikmap=sec_ticker_map()
    except Exception:pass
    ranked=sorted(results,key=lambda x:(x[1]["rs20"]+x[1]["rvol"]*4+x[0]["changePct"]),reverse=True)
    target={x[0]["symbol"] for x in ranked[:45]}
    fundmap={}
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        futs={ex.submit(sec_fundamentals,s,cikmap):s for s in target}
        for fut in cf.as_completed(futs):
            sym=futs[fut]
            try:fundmap[sym]=fut.result()
            except Exception:pass

    researchmap={}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs={ex.submit(fetch_research_evidence,s,cikmap.get(s)):s for s in target}
        for fut in cf.as_completed(futs):
            sym=futs[fut]
            try:researchmap[sym]=fut.result()
            except Exception as exc:
                researchmap[sym]={"revisionConfidence":"低","revisionScore":None,"catalystConfidence":"低","catalystScore":None,"news":[],"recentFilings":[],"researchError":str(exc)[:180]}

    empty={"fundamentalConfidence":"低","revenueGrowth":None,"epsGrowth":None,"fcfMargin":None,"roe":None,"roic":None,"operatingMargin":None,"grossMargin":None,"netMargin":None,"cash":None,"debt":None,"latestFiled":None}
    empty_research={"revisionConfidence":"低","revisionScore":None,"revisionUp4w":0,"revisionDown4w":0,"revisionBreadth4wPct":None,"nextQuarterForecast":None,"nextYearForecast":None,"catalystConfidence":"低","catalystScore":None,"news":[],"recentFilings":[]}
    rows=[score_candidate(stock,t,fundmap.get(stock["symbol"],empty),researchmap.get(stock["symbol"],empty_research)) for stock,t in results]
    calibrate(rows); rows=sorted(rows,key=lambda r:r["dScore"],reverse=True)[:50]
    for i,r in enumerate(rows):r["priority"]="A" if i<10 else "B" if i<25 else "C"
    ai_status=attach_research_reports(rows)
    sectors={}; industries={}; themes={}
    for x in eligible:
        sectors[x["sector"]]=sectors.get(x["sector"],0)+1
        industries[x["industry"]]=industries.get(x["industry"],0)+1
        for th in x["themes"]:themes[th]=themes.get(th,0)+1
    payload={
        "version":"5.0-free","generatedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"mode":mode,
        "universeStats":{**stats,"stage1Eligible":len([x for x in eligible if x["dailyDollarVolume"]>=5_000_000 and x["marketCap"]>=300_000_000]),"deepAttempted":len(seeds),"successfullyProcessed":len(results),"scanFailures":len(failures),"coveragePct":len(results)/len(eligible)*100 if eligible else 0,"latestUniverseRefresh":started.isoformat()},
        "dataGovernance":{"deepScanScope":"Multi-path candidate deep scan, not full OHLCV coverage","marketSource":"Nasdaq public screener + Nasdaq Public Historical OHLCV; Yahoo chart is fallback only","fundamentalSource":"SEC Company Facts","revisionSource":"Nasdaq Earnings Forecast: consensus EPS + 4-week Up/Down revisions","newsSource":"Nasdaq News Headlines; SEC filings are separate regulatory catalysts","historyCache":"GitHub Actions cache (.cache/ohlcv), 1Y bootstrap + 10D incremental refresh","limitations":["Nasdaq public historical bars are treated as raw/unadjusted OHLCV when Yahoo adjusted data is unavailable","Revision Breadth is computed from Up/Down counts and is not EPS consensus percent change","News presence is not treated as bullish by itself","Theme tags are deterministic research labels, not official exchange sectors"]},
        "catalog":{"sectors":sorted([{"name":k,"count":v} for k,v in sectors.items() if k],key=lambda x:(-x["count"],x["name"])),"industries":sorted([{"name":k,"count":v} for k,v in industries.items() if k],key=lambda x:(-x["count"],x["name"])),"themes":sorted([{"name":k,"count":v} for k,v in themes.items()],key=lambda x:(-x["count"],x["name"]))},
        "universeLite":[{"symbol":x["symbol"],"name":x["name"],"sector":x["sector"],"industry":x["industry"],"themes":x["themes"],"price":x["price"],"changePct":x["changePct"],"volume":x["volume"],"marketCap":x["marketCap"],"dailyDollarVolume":x["dailyDollarVolume"]} for x in eligible],
        "aiResearchStatus":ai_status,
        "candidates":rows,"failures":failures[:100]
    }
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    print(f"eligible={len(eligible)} deep={len(results)}/{len(seeds)} failures={len(failures)} output={output}")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--mode",default="manual"); p.add_argument("--output",default=str(ROOT/"site/data/latest.json")); p.add_argument("--force",action="store_true")
    a=p.parse_args(); scan(a.mode,Path(a.output),a.force)

if __name__=="__main__":main()
