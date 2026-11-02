from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
AI_CACHE = ROOT / ".cache" / "ai_research"
AI_CACHE.mkdir(parents=True, exist_ok=True)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/141 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
SEC_HEADERS = {
    "User-Agent": "USMultiHorizonAlphaDashboard/5.1 research contact via GitHub repository",
    "Accept": "application/json,text/plain,*/*",
}

def _clamp(x: float, lo: float = 0, hi: float = 100) -> int:
    return int(round(max(lo, min(hi, x))))

def _num(v: Any) -> float | None:
    try:
        if v is None or v == "--":
            return None
        return float(str(v).replace(",", "").replace("$", "").strip())
    except Exception:
        return None

def _int(v: Any) -> int:
    try:
        if v in (None, "", "--"):
            return 0
        return int(float(v))
    except Exception:
        return 0

def _get_json(url: str, *, sec: bool = False, retries: int = 2, timeout: int = 25) -> dict:
    last = None
    headers = SEC_HEADERS if sec else BROWSER_HEADERS
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(1.2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(str(last) if last else "request failed")

def _forecast_row(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "fiscalEnd": row.get("fiscalEnd"),
        "consensusEPS": _num(row.get("consensusEPSForecast")),
        "highEPS": _num(row.get("highEPSForecast")),
        "lowEPS": _num(row.get("lowEPSForecast")),
        "estimates": _int(row.get("noOfEstimates")),
        "up4w": _int(row.get("up")),
        "down4w": _int(row.get("down")),
    }

def fetch_nasdaq_revision(symbol: str) -> dict:
    empty = {
        "revisionSource": "Nasdaq Earnings Forecast",
        "revisionConfidence": "低",
        "revisionScore": None,
        "revisionUp4w": 0,
        "revisionDown4w": 0,
        "revisionBreadth4wPct": None,
        "nextQuarterForecast": None,
        "nextYearForecast": None,
        "revisionFetchedAt": None,
    }
    try:
        j = _get_json(f"https://api.nasdaq.com/api/analyst/{quote(symbol)}/earnings-forecast", retries=3)
        data = j.get("data") or {}
        qrows = ((data.get("quarterlyForecast") or {}).get("rows") or [])
        yrows = ((data.get("yearlyForecast") or {}).get("rows") or [])
        q = _forecast_row(qrows[0] if qrows else None)
        y = _forecast_row(yrows[0] if yrows else None)

        focus = [_forecast_row(x) for x in (qrows[:2] + yrows[:2])]
        focus = [x for x in focus if x]
        up = sum(x["up4w"] for x in focus)
        down = sum(x["down4w"] for x in focus)
        estimates = sum(x["estimates"] for x in focus)
        breadth = ((up - down) / estimates * 100) if estimates else None

        # Directional revision factor only. A neutral consensus remains 50.
        score = _clamp(50 + (breadth or 0) * 1.6, 25, 75) if focus else None
        confidence = "高" if estimates >= 20 else "中" if estimates >= 6 else "低"

        return {
            **empty,
            "revisionConfidence": confidence,
            "revisionScore": score,
            "revisionUp4w": up,
            "revisionDown4w": down,
            "revisionBreadth4wPct": breadth,
            "nextQuarterForecast": q,
            "nextYearForecast": y,
            "revisionFetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {**empty, "revisionError": str(exc)[:180]}

def fetch_nasdaq_news(symbol: str, limit: int = 6) -> dict:
    out = {
        "newsSource": "Nasdaq News Headlines",
        "newsConfidence": "低",
        "news": [],
        "newsFetchedAt": None,
    }
    try:
        q = quote(f"{symbol}|stocks", safe="")
        j = _get_json(f"https://api.nasdaq.com/api/news/topic/articlebysymbol?q={q}", retries=3)
        rows = ((j.get("data") or {}).get("rows") or [])
        items = []
        seen = set()
        symbol_l = symbol.lower()
        for x in rows:
            related = [str(v).lower() for v in (x.get("related_symbols") or [])]
            primary = str(x.get("primarysymbol") or "").lower()
            if primary != symbol_l and not any(v.startswith(symbol_l + "|") for v in related):
                continue
            key = x.get("id") or x.get("url") or x.get("title")
            if key in seen:
                continue
            seen.add(key)
            u = str(x.get("url") or "")
            if u.startswith("/"):
                u = "https://www.nasdaq.com" + u
            items.append({
                "title": x.get("title"),
                "publisher": x.get("publisher"),
                "created": x.get("created"),
                "ago": x.get("ago"),
                "description": x.get("description"),
                "url": u,
                "primarySymbol": x.get("primarysymbol"),
            })
            if len(items) >= limit:
                break
        return {
            **out,
            "newsConfidence": "中" if items else "低",
            "news": items,
            "newsFetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {**out, "newsError": str(exc)[:180]}

def fetch_sec_filings(symbol: str, cik: str | None, limit: int = 6) -> dict:
    out = {
        "filingSource": "SEC Submissions",
        "filingConfidence": "低",
        "recentFilings": [],
        "filingsFetchedAt": None,
    }
    if not cik:
        return out
    try:
        j = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json", sec=True, retries=2)
        recent = ((j.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        accepted = {"8-K", "10-Q", "10-K", "6-K", "20-F"}
        items = []
        for i, form in enumerate(forms):
            if form not in accepted:
                continue
            try:
                acc = str(accessions[i])
                doc = str(docs[i])
                date = str(dates[i])
            except Exception:
                continue
            acc_clean = acc.replace("-", "")
            sec_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{doc}"
            items.append({
                "form": form,
                "filingDate": date,
                "accessionNumber": acc,
                "url": sec_url,
            })
            if len(items) >= limit:
                break
        return {
            **out,
            "filingConfidence": "高" if items else "低",
            "recentFilings": items,
            "filingsFetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {**out, "filingError": str(exc)[:180]}

def fetch_research_evidence(symbol: str, cik: str | None) -> dict:
    revision = fetch_nasdaq_revision(symbol)
    news = fetch_nasdaq_news(symbol)
    filings = fetch_sec_filings(symbol, cik)

    has_event = bool(news.get("news") or filings.get("recentFilings"))
    # News/filing existence is evidence availability, not bullish direction.
    catalyst_score = 50 if has_event else None
    catalyst_confidence = (
        "高" if filings.get("recentFilings")
        else "中" if news.get("news")
        else "低"
    )
    return {
        **revision,
        **news,
        **filings,
        "catalystScore": catalyst_score,
        "catalystConfidence": catalyst_confidence,
    }

def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "資料不足"
    if isinstance(v, float):
        return f"{v:.2f}{suffix}"
    return f"{v}{suffix}"

def deterministic_report(c: dict) -> str:
    q = c.get("nextQuarterForecast") or {}
    y = c.get("nextYearForecast") or {}
    news = c.get("news") or []
    filings = c.get("recentFilings") or []

    news_lines = "\n".join(
        f"- [NEWS-{i+1}] {x.get('created') or '日期不足'}｜{x.get('publisher') or '來源不足'}｜{x.get('title') or '標題不足'}"
        for i, x in enumerate(news[:5])
    ) or "- 資料不足"

    filing_lines = "\n".join(
        f"- [FIL-{i+1}] {x.get('filingDate') or '日期不足'}｜SEC {x.get('form') or 'Form 資料不足'}"
        for i, x in enumerate(filings[:5])
    ) or "- 資料不足"

    return f"""## 執行摘要
此研究只使用本次掃描已驗證的 Market / SEC / Nasdaq Analyst / Nasdaq News 證據。未提供的商業模式、供應鏈細節或估值資料不作推測。

## 盈利預期修正
- [REV] 過去4週 Up / Down：{c.get('revisionUp4w', 0)} / {c.get('revisionDown4w', 0)}
- [REV] Revision Breadth：{_fmt(c.get('revisionBreadth4wPct'), '%')}
- [REV] 下一季度 Consensus EPS：{_fmt(q.get('consensusEPS'))}；分析師數：{_fmt(q.get('estimates'))}
- [REV] 下一財年 Consensus EPS：{_fmt(y.get('consensusEPS'))}；分析師數：{_fmt(y.get('estimates'))}
- Revision Confidence：{c.get('revisionConfidence') or '低'}

## 財務與質量
- [SEC] Revenue Growth：{_fmt(c.get('revenueGrowth'), '%')}
- [SEC] EPS Growth：{_fmt(c.get('epsGrowth'), '%')}
- [SEC] FCF Margin：{_fmt(c.get('fcfMargin'), '%')}
- [SEC] Operating Margin：{_fmt(c.get('operatingMargin'), '%')}
- [SEC] ROE / ROIC：{_fmt(c.get('roe'), '%')} / {_fmt(c.get('roic'), '%')}

## 技術與資金
- [MKT] S / M / G：{_fmt(c.get('sScore'))} / {_fmt(c.get('mScore'))} / {_fmt(c.get('gScore'))}
- [MKT] RS20：{_fmt(c.get('rs20'))}；RVOL：{_fmt(c.get('rvol'))}
- [MKT] WaveTrend：{_fmt(c.get('waveTrendScore'))}；MF：{_fmt(c.get('mfScore'))}；Indicator：{_fmt(c.get('indicatorScore'))}
- [MKT] 1M / 3M / 6M Return：{_fmt(c.get('return1m'), '%')} / {_fmt(c.get('return3m'), '%')} / {_fmt(c.get('return6m'), '%')}

## 最新新聞證據
{news_lines}

## SEC 事件
{filing_lines}

## 估值
- Forward P/E：{_fmt(c.get('forwardPE'))}
- 其他可驗證估值資料：資料不足

## 商業模式 / 需求 / 供應鏈
資料不足。現有免費資料層未提供足夠公司業務描述及供應鏈證據，因此不憑模型記憶補寫。

## 主要風險與證據缺口
- Nasdaq News 是新聞聚合證據，不代表文章觀點正確，也不自動當作利多。
- Nasdaq Analyst Revision 反映過去4週 Up / Down 次數；它不是「EPS 共識百分比變化」。
- Nasdaq Historical fallback 為 raw / unadjusted OHLCV。
- 未取得的估值、供應鏈、管理層指引或 TAM 一律視為資料不足。
"""

def _evidence_payload(c: dict) -> dict:
    keep = [
        "symbol","name","sector","industry","themes","price","marketCap",
        "sScore","mScore","gScore","dScore","rs20","rvol","rsi14","atr14",
        "return1m","return3m","return6m","return1y","waveTrendScore","waveTrendState",
        "mfScore","mfState","mfDivergence","indicatorScore","revenueGrowth","epsGrowth",
        "fcfMargin","operatingMargin","grossMargin","netMargin","roe","roic","cash","debt",
        "revisionSource","revisionConfidence","revisionScore","revisionUp4w","revisionDown4w",
        "revisionBreadth4wPct","nextQuarterForecast","nextYearForecast","news","recentFilings",
        "forwardPE","riskScore","trueAlphaScore","momentumScore","dataConfidence",
    ]
    return {k: c.get(k) for k in keep}

def _fingerprint(c: dict) -> str:
    raw = json.dumps(_evidence_payload(c), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _cache_path(symbol: str) -> Path:
    return AI_CACHE / f"{symbol}.json"

def _load_ai_cache(c: dict) -> dict | None:
    p = _cache_path(c["symbol"])
    if not p.exists():
        return None
    try:
        j = json.loads(p.read_text())
        if j.get("fingerprint") != _fingerprint(c):
            return None
        created = dt.datetime.fromisoformat(str(j["generatedAt"]).replace("Z", "+00:00"))
        if dt.datetime.now(dt.timezone.utc) - created > dt.timedelta(hours=30):
            return None
        return j
    except Exception:
        return None

def _save_ai_cache(c: dict, report: str, model: str) -> None:
    p = _cache_path(c["symbol"])
    p.write_text(json.dumps({
        "fingerprint": _fingerprint(c),
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": model,
        "report": report,
    }, ensure_ascii=False))

def gemini_report(c: dict, api_key: str, model: str = "gemini-3.6-flash") -> str:
    evidence = json.dumps(_evidence_payload(c), ensure_ascii=False, separators=(",", ":"), default=str)
    prompt = f"""你是機構級美股研究助理。請只使用下方 EVIDENCE JSON，禁止使用模型記憶補充公司事實。
如某一項沒有證據，必須寫「資料不足」。不要把新聞標題當成已證實事實；不要把新聞存在本身視為利多。
所有結論都要用以下來源標籤：Market=[MKT]、SEC fundamentals=[SEC]、Nasdaq analyst revisions=[REV]、news=[NEWS-n]、filings=[FIL-n]。
輸出繁體中文 Markdown，依次包含：
1. 執行摘要
2. 商業模式與收入驅動（無證據就資料不足）
3. 產業需求 / 供應鏈（無證據就資料不足）
4. 盈利預期修正
5. 財務質量
6. 估值
7. 技術與資金
8. 最新催化劑
9. 主要風險 / 反證
10. 證據缺口
最後加一句：此報告不是投資建議。

EVIDENCE JSON:
{evidence}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model)}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": 1400,
        },
    }
    r = requests.post(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    j = r.json()
    parts = (((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "\n".join(str(x.get("text") or "") for x in parts).strip()
    if not text:
        raise RuntimeError("Gemini returned empty text")
    return text

def attach_research_reports(rows: list[dict]) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
    limit = int(os.getenv("AI_RESEARCH_LIMIT", "12"))
    generated = 0
    cached = 0
    failures = 0

    for i, c in enumerate(rows):
        c["evidenceResearch"] = deterministic_report(c)
        c["aiResearch"] = c["evidenceResearch"]
        c["aiResearchMode"] = "Deterministic evidence synthesis"
        c["aiResearchModel"] = None
        c["aiResearchError"] = None

        if not api_key or i >= limit:
            continue

        hit = _load_ai_cache(c)
        if hit:
            c["aiResearch"] = hit["report"]
            c["aiResearchMode"] = "Gemini cached synthesis"
            c["aiResearchModel"] = hit.get("model")
            cached += 1
            continue

        try:
            report = gemini_report(c, api_key, model)
            c["aiResearch"] = report
            c["aiResearchMode"] = "Gemini evidence-grounded synthesis"
            c["aiResearchModel"] = model
            _save_ai_cache(c, report, model)
            generated += 1
            time.sleep(0.8)
        except Exception as exc:
            c["aiResearchError"] = str(exc)[:220]
            failures += 1

    return {
        "enabled": bool(api_key),
        "provider": "Gemini Developer API Free Tier" if api_key else "No AI secret configured",
        "model": model if api_key else None,
        "generationLimitPerScan": limit if api_key else 0,
        "generated": generated,
        "cacheHits": cached,
        "failures": failures,
        "fallback": "Deterministic evidence synthesis",
    }
