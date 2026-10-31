# US Multi-Horizon Alpha Dashboard 5.0 — Free Runtime Edition

免費運行架構：

- GitHub Pages：靜態 Dashboard，掃描失敗時網站仍可開啟
- GitHub Actions：美股掃描、WT/MF、S/M/G、Sector/Theme 及自動部署
- GitHub Actions cache：Yahoo Daily OHLCV 快取
- Nasdaq public screener：Universe + Sector / Industry
- Yahoo Finance public chart：1Y bootstrap + 10D incremental
- SEC Company Facts：年度基本面

## 主要功能

- S / M / G 獨立評分
- SM / MG / SMG 共振
- Research Watchlist 最多 50 隻
- WaveTrend 9/12/3
- Market Cipher MF 5/5/60 + Price Pivot 5/5 divergence
- Indicator Composite = 55% WT + 45% MF
- Sector / Industry / Theme
- Interactive Screener
- 自訂排序與 CSV
- Universe / Deep Processed / Failures / True Coverage

## 數據原則

不會把未取得的 analyst revision、新聞、估值或財務值填成假數字。Deep Scan 亦不會冒充全市場 OHLCV 覆蓋。

## 排程

America/New_York：
- 08:30 交易日
- 10:00 交易日
- 16:45 交易日
- 11:00 星期六

Daily WT/MF 以 1D Yahoo bars 計算。
