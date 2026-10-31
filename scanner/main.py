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

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
HIST = CACHE / "ohlcv"
HIST.mkdir(parents=True, exist_ok=True)

UA = "USMultiHorizonAlphaDashboard/5.0 free-github-actions research tool"
HEADERS = {"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}

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

def http_json(url:str,retries=3,timeout=18)->Any:
    last=None
    for attempt in range(retries):
        try:
            r=requests.get(url,headers=HEADERS,timeout=timeout)
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

def fetch_bars(symbol:str,range_:str)->dict:
    j=http_json(yahoo_url(symbol,range_)); z=(j.get("chart",{}).get("result") or [None])[0]
    if not z: raise RuntimeError("no chart")
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
    return {"bars":bars,"marketDataAt":dt.datetime.fromtimestamp(mt,dt.timezone.utc).isoformat() if mt else None,"regularMarketPrice":nullable(meta.get("regularMarketPrice")),"splits":len((z.get("events") or {}).get("splits") or {}),"dividends":len((z.get("events") or {}).get("dividends") or {})}

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
        out={"symbol":symbol,"bars":d["bars"][-370:],"marketDataAt":d["marketDataAt"],"refreshedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"splits":d["splits"],"dividends":d["dividends"],"mode":"bootstrap"}
    else:
        d=fetch_bars(symbol,"10d")
        if d["splits"]:
            d=fetch_bars(symbol,"1y"); bars=d["bars"][-370:]; mode="split-rebuild"
        else:
            merged={b["date"]:b for b in cached["bars"]}; merged.update({b["date"]:b for b in d["bars"]})
            bars=[merged[k] for k in sorted(merged)][-370:]; mode="incremental"
        out={"symbol":symbol,"bars":bars,"marketDataAt":d["marketDataAt"] or cached.get("marketDataAt"),"refreshedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"splits":max(cached.get("splits",0),d["splits"]),"dividends":max(cached.get("dividends",0),d["dividends"]),"mode":mode}
    path.write_text(json.dumps(out,separators=(",",":"))); return out

def series_from_cache(cache:dict)->dict:
    c=[];h=[];l=[];v=[];dates=[];rawc=[]
    for b in cache["bars"]:
        close=float(b["close"]); adj=float(b.get("adjClose",close)); factor=adj/close if close else 1
        c.append(adj); h.append(float(b["high"])*factor); l.append(float(b["low"])*factor); v.append(float(b["volume"])); rawc.append(close); dates.append(b["date"])
    return {"c":c,"h":h,"l":l,"v":v,"dates":dates,"rawClose":rawc,"lastPrice":rawc[-1],"marketDataAt":cache.get("marketDataAt"),"cacheMode":cache.get("mode")}
