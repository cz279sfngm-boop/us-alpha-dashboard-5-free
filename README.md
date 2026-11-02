# US Multi-Horizon Alpha Dashboard 5.1 — Free Research Runtime

Production dashboard:

https://cz279sfngm-boop.github.io/us-alpha-dashboard-5-free/

## Free architecture

- **GitHub Pages** — static dashboard; a scan failure does not take the site runtime down.
- **GitHub Actions** — scheduled scanning, technical calculation, evidence gathering and deployment.
- **GitHub Actions cache** — historical OHLCV and optional AI report cache.
- **Nasdaq public screener** — US universe, price/volume, Sector and Industry.
- **Nasdaq Public Historical** — primary daily OHLCV on GitHub runners; raw/unadjusted.
- **Yahoo chart** — fallback only; GitHub-hosted runners may receive 429.
- **Nasdaq Earnings Forecast** — Consensus EPS plus analyst Up/Down revisions over the last four weeks.
- **Nasdaq News Headlines** — symbol-linked headline evidence.
- **Nasdaq / Quotemedia SEC Filings** — runtime regulatory filing evidence; direct SEC endpoints are blocked with 403 on GitHub-hosted Azure runners.
- **SEC Company Facts** — attempted only where runtime access is available.

## Research governance

The system distinguishes evidence from synthesis.

### Analyst revision

The revision factor uses the Nasdaq Earnings Forecast fields:

- Consensus EPS Forecast
- Number of Estimates
- Number of Revisions Up over the last 4 weeks
- Number of Revisions Down over the last 4 weeks

The dashboard computes a directional **Revision Breadth** from Up/Down counts. It does **not** claim this is the percentage change in consensus EPS.

### News

News is evidence only. The existence of a headline does not automatically create a bullish score.

### Regulatory filings

Recent 8-K / 10-Q / 10-K / 6-K / 20-F filings are surfaced separately. They are high-quality event evidence but are not automatically classified as positive or negative.

### AI Alpha Research

Every candidate receives a deterministic evidence-grounded research note.

If the repository secret `GEMINI_API_KEY` is configured, the top candidates can also receive Gemini synthesis. The model prompt explicitly prohibits using model-memory company facts and requires unsupported sections to say **資料不足**.

The API key is never written into the repository or GitHub Pages.

## Optional Gemini setup

Repository secret name:

`GEMINI_API_KEY`

Optional scanner environment:

- `GEMINI_MODEL=gemini-3.6-flash`
- `AI_RESEARCH_LIMIT=12`

Without this secret the scanner continues to work and uses deterministic evidence synthesis.

## True on-demand AI

The repository contains:

`supabase/functions/alpha-research/`

This is a secure Edge Function scaffold for click-to-generate AI research. It is designed for:

- Supabase JWT verification
- server-side Gemini secret
- allowed-origin restriction
- an evidence-field allowlist
- no model-memory company facts

The GitHub Pages frontend should never call Gemini directly with a browser-exposed secret.

## Core functions

- S / M / G independent scores
- SM / MG / SMG resonance pools
- Research Watchlist up to 50 names
- WaveTrend 9/12/3
- Market Cipher MF 5/5/60 + confirmed price-pivot divergence
- Indicator Composite = 55% WT + 45% MF
- Sector / Industry / Theme discovery across the eligible universe
- Interactive Screener
- Revision Score / Catalyst Evidence filters
- Evidence detail: analyst revisions, Nasdaq news, regulatory filings
- AI Alpha Research / deterministic fallback
- custom sorting and CSV
- Universe / Deep Processed / Failures / True Coverage

## Scan schedule

America/New_York:

- 08:30 weekdays
- 10:00 weekdays
- 16:45 weekdays
- 11:00 Saturday

## Important limitations

- Scores are ranking tools, not win probabilities.
- Nasdaq historical fallback is raw/unadjusted OHLCV.
- Direct SEC data access may be unavailable from GitHub-hosted runners.
- Free public endpoints can change or rate-limit.
- Theme tags are deterministic research labels, not official exchange classifications.
- No missing financial, analyst, news or valuation field is silently fabricated.
