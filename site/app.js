let DB = null;
let activeTab = "ALL";
let filtered = [];
let currentDetailSymbol = null;
let instantSupabase = null;

const SUPABASE_URL = "https://eftqdcninulszcttsgxv.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_Yhita1G8gXzw-NwZYNm2kQ_TTJUKDD_";

const $ = (id) => document.getElementById(id);

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[m]));
}

function num(v, d = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "資料不足";
  return Number(v).toFixed(d);
}

function pct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "資料不足";
  const n = Number(v);
  return (n >= 0 ? "+" : "") + n.toFixed(1) + "%";
}

function money(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "資料不足";
  const n = Number(v);
  if (Math.abs(n) >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
  if (Math.abs(n) >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  return "$" + n.toFixed(0);
}

function metric(label, value) {
  return '<div class="metric"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong></div>';
}

async function boot() {
  try {
    const r = await fetch("data/latest.json", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    DB = await r.json();
    renderAll();
    $("status").textContent = "最後掃描：" + new Date(DB.generatedAt).toLocaleString("zh-HK") + " · " + (DB.mode || "scan");
  } catch (e) {
    $("status").classList.add("error");
    $("status").textContent = "尚未有可用掃描資料。這不是「0隻候選」，而是資料載入失敗。";
  }
}

function fillSelect(id, arr) {
  const el = $(id);
  const first = el.options.length ? el.options[0].outerHTML : '<option value="">全部</option>';
  el.innerHTML = first + arr.map((x) =>
    '<option value="' + esc(x.name) + '">' + esc(x.name) + " (" + x.count + ")</option>"
  ).join("");
}

function updateIndustryOptions() {
  const sector = $("sector").value;
  const counts = {};
  (DB?.universeLite || []).forEach((x) => {
    if ((!sector || x.sector === sector) && x.industry) {
      counts[x.industry] = (counts[x.industry] || 0) + 1;
    }
  });
  const arr = Object.entries(counts)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const previous = $("industry").value;
  fillSelect("industry", arr);
  $("industry").value = arr.some((x) => x.name === previous) ? previous : "";
}

function renderSectorUniverse() {
  const sector = $("sector").value;
  const industry = $("industry").value;
  const theme = $("theme").value;
  const deepSet = new Set((DB.candidates || []).map((x) => x.symbol));

  let rows = (DB.universeLite || []).filter((x) =>
    (!sector || x.sector === sector) &&
    (!industry || x.industry === industry) &&
    (!theme || (x.themes || []).includes(theme))
  );

  const deepMatches = rows.filter((x) => deepSet.has(x.symbol)).length;
  const coverage = rows.length ? (deepMatches / rows.length) * 100 : 0;

  $("sectorUniverseMeta").innerHTML =
    "<b>Universe Match：" + rows.length + "</b> · Deep Watchlist Match：" + deepMatches +
    " · Deep Coverage：" + coverage.toFixed(1) +
    "% · <span>Universe 搜尋涵蓋全部 eligible 股票；S/M/G、WT/MF 只對 Deep Processed 股票提供。</span>";

  rows.sort((a, b) => (b.dailyDollarVolume || 0) - (a.dailyDollarVolume || 0));
  const shown = rows.slice(0, 40);

  $("sectorUniverseBody").innerHTML = shown.length
    ? shown.map((x) =>
        '<tr>' +
        '<td><div class="sym">' + esc(x.symbol) + '</div><div class="name">' + esc(x.name) + '</div></td>' +
        "<td>$" + num(x.price, 2) + "</td>" +
        "<td>" + pct(x.changePct) + "</td>" +
        "<td>" + money(x.marketCap) + "</td>" +
        "<td>" + money(x.dailyDollarVolume) + "</td>" +
        "<td><div>" + esc(x.sector || "Unclassified") + '</div><div class="classify">' + esc(x.industry || "") + "</div></td>" +
        "<td>" + esc((x.themes || []).join(", ") || "—") + "</td>" +
        "<td>" + (deepSet.has(x.symbol) ? '<span class="pill WATCH">Deep</span>' : "—") + "</td>" +
        "</tr>"
      ).join("")
    : '<tr><td colspan="8" class="empty">這個 Sector / Industry / Theme 在目前 eligible universe 沒有匹配股票。</td></tr>';
}

function renderTabs() {
  const tabs = ["ALL", "S", "M", "G", "SM", "MG", "SMG"];
  $("tabs").innerHTML = tabs.map((t) =>
    '<button class="' + (t === activeTab ? "active" : "") + '" data-tab="' + t + '">' +
    (t === "ALL" ? "全部研究" : t) + "</button>"
  ).join("");

  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.onclick = () => {
      activeTab = b.dataset.tab;
      renderTabs();
      apply();
    };
  });
}

function getVal(id) {
  const v = $(id).value;
  return v === "" ? null : Number(v);
}

function apply() {
  if (!DB) return;
  renderSectorUniverse();

  const sector = $("sector").value;
  const industry = $("industry").value;
  const theme = $("theme").value;
  const horizon = $("horizon").value;

  const minCap = getVal("minCap");
  const minPrice = getVal("minPrice");
  const maxPrice = getVal("maxPrice");
  const minRvol = getVal("minRvol");
  const minRS = getVal("minRS");
  const minRev = getVal("minRev");
  const minRoic = getVal("minRoic");
  const minWT = getVal("minWT");
  const minMF = getVal("minMF");
  const minInd = getVal("minInd");
  const minRevision = getVal("minRevision");
  const requireCatalyst = $("requireCatalyst").value;

  filtered = (DB.candidates || []).filter((c) => {
    const tags = String(c.pool || "").split(",");
    if (activeTab !== "ALL" && !tags.includes(activeTab)) return false;
    if (horizon !== "ALL" && !tags.includes(horizon)) return false;
    if (sector && c.sector !== sector) return false;
    if (industry && c.industry !== industry) return false;
    if (theme && !(c.themes || []).includes(theme)) return false;
    if (minCap !== null && c.marketCap / 1e6 < minCap) return false;
    if (minPrice !== null && c.price < minPrice) return false;
    if (maxPrice !== null && c.price > maxPrice) return false;
    if (minRvol !== null && c.rvol < minRvol) return false;
    if (minRS !== null && c.rs20 < minRS) return false;
    if (minRev !== null && (c.revenueGrowth === null || c.revenueGrowth < minRev)) return false;
    if (minRoic !== null && (c.roic === null || c.roic < minRoic)) return false;
    if (minWT !== null && c.waveTrendScore < minWT) return false;
    if (minMF !== null && c.mfScore < minMF) return false;
    if (minInd !== null && c.indicatorScore < minInd) return false;
    if (minRevision !== null && (c.revisionScore === null || c.revisionScore < minRevision)) return false;
    if (requireCatalyst === "yes" && c.catalystConfidence === "低") return false;
    return true;
  });

  const key = $("sortKey").value;
  const dir = $("sortDir").value;
  filtered.sort((a, b) => {
    const av = Number(a[key] ?? -Infinity);
    const bv = Number(b[key] ?? -Infinity);
    return dir === "desc" ? bv - av : av - bv;
  });

  renderTable();
}

function renderTable() {
  $("resultMeta").textContent =
    "顯示 " + filtered.length + " / " + (DB.candidates || []).length +
    " 隻 Research Watchlist · 排序只改顯示次序，不改 Pool 資格";

  $("tbody").innerHTML = filtered.length
    ? filtered.map((c) =>
        '<tr data-sym="' + esc(c.symbol) + '">' +
        '<td><div class="sym">' + esc(c.symbol) + '</div><div class="name">' + esc(c.name) + "</div></td>" +
        "<td>$" + num(c.price, 2) + "</td>" +
        '<td class="score">' + c.sScore + "</td>" +
        '<td class="score">' + c.mScore + "</td>" +
        '<td class="score">' + c.gScore + "</td>" +
        '<td class="score">' + c.dScore + "</td>" +
        "<td>" + num(c.rvol, 2) + "×</td>" +
        "<td>" + num(c.rs20) + "</td>" +
        '<td class="score">' + c.waveTrendScore + "</td>" +
        '<td class="score">' + c.mfScore + "</td>" +
        '<td class="score">' + c.indicatorScore + "</td>" +
        "<td>" + (c.revisionScore == null ? "資料不足" : c.revisionScore + " · " + (c.revisionUp4w || 0) + "↑/" + (c.revisionDown4w || 0) + "↓") + "</td>" +
        "<td>" + pct(c.revenueGrowth) + "</td>" +
        "<td>" + pct(c.roic) + "</td>" +
        "<td><div>" + esc(c.sector || "Unclassified") + '</div><div class="classify">' + esc(c.industry || "") + "</div></td>" +
        "<td>" + esc(c.pool || "Research only") + "</td>" +
        '<td><span class="pill ' + esc(c.status) + '">' + esc(c.status) + "</span></td>" +
        "</tr>"
      ).join("")
    : '<tr><td colspan="17" class="empty">目前條件沒有匹配股票。這只代表 Research Watchlist 篩選結果為0，不代表沒有市場資料。</td></tr>';

  document.querySelectorAll("#tbody tr[data-sym]").forEach((tr) => {
    tr.onclick = () => detail(tr.dataset.sym);
  });
}

function detail(sym) {
  const c = (DB.candidates || []).find((x) => x.symbol === sym);
  if (!c) return;
  currentDetailSymbol = sym;

  $("detailPanel").hidden = false;
  $("detailTitle").textContent = c.symbol + " · Score Explainability";
  $("detailSub").textContent = c.name + " · " + (c.sector || "Unclassified") + " · " + (c.industry || "");

  const items = [
    ["Market Cap", money(c.marketCap)],
    ["Price", "$" + num(c.price, 2)],
    ["S / M / G", c.sScore + " / " + c.mScore + " / " + c.gScore],
    ["Consensus", c.dScore],
    ["RVOL", num(c.rvol, 2) + "×"],
    ["RS20", num(c.rs20)],
    ["WT Score", c.waveTrendScore],
    ["WT1 / WT2", num(c.wt1) + " / " + num(c.wt2)],
    ["WT-VWAP", num(c.wtVwap)],
    ["WT Cross", c.wtCross + (c.wtBarsSinceCross == null ? "" : " · " + c.wtBarsSinceCross + " bars")],
    ["MF Score", c.mfScore],
    ["MF Value", num(c.mfValue)],
    ["MF Divergence", c.mfDivergence + (c.mfDivBarsAgo == null ? "" : " · " + c.mfDivBarsAgo + " bars")],
    ["Indicator", c.indicatorScore],
    ["Revision Score", c.revisionScore == null ? "資料不足" : c.revisionScore],
    ["Revision Up / Down 4W", (c.revisionUp4w || 0) + " / " + (c.revisionDown4w || 0)],
    ["Revision Breadth 4W", pct(c.revisionBreadth4wPct)],
    ["Revision Confidence", c.revisionConfidence || "低"],
    ["Catalyst Confidence", c.catalystConfidence || "低"],
    ["Revenue Growth", pct(c.revenueGrowth)],
    ["EPS Growth", pct(c.epsGrowth)],
    ["Operating Margin", pct(c.operatingMargin)],
    ["ROE", pct(c.roe)],
    ["ROIC", pct(c.roic)],
    ["20D Dollar Volume", money(c.avgDollarTurnover20)],
    ["52W Range", num(c.low52, 2) + "–" + num(c.high52, 2)],
    ["Themes", (c.themes || []).join(", ") || "—"]
  ];

  $("detailGrid").innerHTML = items.map((x) =>
    "<div><span>" + esc(x[0]) + "</span><b>" + esc(x[1]) + "</b></div>"
  ).join("");

  const q = c.nextQuarterForecast || {};
  const y = c.nextYearForecast || {};
  const revisionItems = [
    ["Next Quarter EPS", q.consensusEPS == null ? "資料不足" : num(q.consensusEPS, 2)],
    ["Quarter Estimates", q.estimates == null ? "資料不足" : q.estimates],
    ["Quarter Up / Down", (q.up4w || 0) + " / " + (q.down4w || 0)],
    ["Next FY EPS", y.consensusEPS == null ? "資料不足" : num(y.consensusEPS, 2)],
    ["FY Estimates", y.estimates == null ? "資料不足" : y.estimates],
    ["FY Up / Down", (y.up4w || 0) + " / " + (y.down4w || 0)]
  ];
  $("revisionGrid").innerHTML = revisionItems.map((x) =>
    "<div><span>" + esc(x[0]) + "</span><b>" + esc(x[1]) + "</b></div>"
  ).join("");

  const news = c.news || [];
  $("newsList").innerHTML = news.length ? news.map((x, i) =>
    '<a class="evidenceItem" href="' + esc(x.url || "#") + '" target="_blank" rel="noopener noreferrer">' +
    '<b>[NEWS-' + (i + 1) + "] " + esc(x.title || "Untitled") + "</b>" +
    "<span>" + esc((x.publisher || "來源不足") + " · " + (x.created || x.ago || "日期不足")) + "</span></a>"
  ).join("") : '<div class="evidenceEmpty">資料不足</div>';

  const filings = c.recentFilings || [];
  $("filingList").innerHTML = filings.length ? filings.map((x, i) =>
    '<a class="evidenceItem" href="' + esc(x.url || "#") + '" target="_blank" rel="noopener noreferrer">' +
    '<b>[FIL-' + (i + 1) + "] SEC " + esc(x.form || "Form") + "</b>" +
    "<span>" + esc(x.filingDate || "日期不足") + "</span></a>"
  ).join("") : '<div class="evidenceEmpty">資料不足</div>';

  $("researchEvidenceMeta").textContent =
    "Revision: " + (c.revisionSource || "資料不足") + " · News: " + (c.newsSource || "資料不足") +
    " · Filing: " + (c.filingSource || "資料不足");
  $("aiModeChip").textContent = c.aiResearchMode || "Evidence synthesis";
  $("aiResearch").textContent = c.aiResearch || c.evidenceResearch || "資料不足";
  $("instantAiStatus").textContent = "預生成 Research 已載入；按下按鈕可用最新 evidence 即時重新生成。";
  $("instantResearchBtn").disabled = false;
  $("instantResearchBtn").textContent = "即時生成 AI Research";

  $("detailPanel").scrollIntoView({ behavior: "smooth" });
}


async function getInstantSupabase() {
  if (!window.supabase || !window.supabase.createClient) {
    throw new Error("Supabase client library 未載入");
  }

  if (!instantSupabase) {
    instantSupabase = window.supabase.createClient(
      SUPABASE_URL,
      SUPABASE_PUBLISHABLE_KEY,
      {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: false
        }
      }
    );
  }

  const { data: sessionData, error: sessionError } = await instantSupabase.auth.getSession();
  if (sessionError) throw sessionError;

  if (!sessionData.session) {
    const { data, error } = await instantSupabase.auth.signInAnonymously();
    if (error) throw error;
    if (!data.session) throw new Error("匿名登入未建立 session");
  }

  return instantSupabase;
}

async function runInstantResearch() {
  if (!currentDetailSymbol || !DB) return;

  const candidate = (DB.candidates || []).find((x) => x.symbol === currentDetailSymbol);
  if (!candidate) return;

  const btn = $("instantResearchBtn");
  const status = $("instantAiStatus");
  btn.disabled = true;
  btn.textContent = "生成中…";
  status.textContent = "正在建立安全 session 並以目前 evidence 重新生成研究報告…";

  try {
    const client = await getInstantSupabase();
    const { data, error } = await client.functions.invoke("alpha-research", {
      body: { evidence: candidate }
    });

    if (error) throw error;
    if (!data || !data.report) {
      throw new Error(data?.error || "AI gateway 沒有回傳報告");
    }

    $("aiResearch").textContent = data.report;
    $("aiModeChip").textContent = "Instant · " + (data.model || "Gemini");
    status.textContent =
      "即時 AI Research 已生成 · " +
      new Date(data.generatedAt || Date.now()).toLocaleString("zh-HK") +
      " · grounded=" + String(data.grounded === true);
    btn.textContent = "重新生成 AI Research";
  } catch (error) {
    const message = String(error?.message || error || "Unknown error");
    status.textContent =
      "即時模式尚未可用：" + message +
      "。預生成 Research 仍可正常閱讀。";
    btn.textContent = "重試即時 AI Research";
  } finally {
    btn.disabled = false;
  }
}


function readFilters() {
  const ids = ["sector","industry","theme","horizon","minCap","minPrice","maxPrice","minRvol","minRS","minRev","minRoic","minWT","minMF","minInd","minRevision","requireCatalyst","sortKey","sortDir"];
  return Object.fromEntries(ids.map((id) => [id, $(id).value]));
}

function saveFilters() {
  localStorage.setItem("alpha5-free-filters", JSON.stringify(readFilters()));
}

function restoreFilters() {
  try {
    const values = JSON.parse(localStorage.getItem("alpha5-free-filters") || "{}");
    Object.entries(values).forEach(([k, v]) => {
      if ($(k)) $(k).value = v;
    });
    updateIndustryOptions();
    apply();
  } catch (e) {}
}

function resetFilters() {
  ["sector","industry","theme"].forEach((id) => $(id).value = "");
  $("horizon").value = "ALL";
  ["minCap","minPrice","maxPrice","minRvol","minRS","minRev","minRoic","minWT","minMF","minInd","minRevision"].forEach((id) => $(id).value = "");
  $("requireCatalyst").value = "";
  activeTab = "ALL";
  updateIndustryOptions();
  renderTabs();
  apply();
}

function exportCsv() {
  if (!filtered.length) return;
  const cols = ["symbol","name","price","sScore","mScore","gScore","dScore","rvol","rs20","waveTrendScore","mfScore","indicatorScore","revisionScore","revisionUp4w","revisionDown4w","revisionBreadth4wPct","revenueGrowth","roic","sector","industry","pool","status"];
  const lines = [
    cols.join(","),
    ...filtered.map((c) => cols.map((k) => '"' + String(c[k] ?? "").replace(/"/g, '""') + '"').join(","))
  ];
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  a.download = "alpha-dashboard-results.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function bindEvents() {
  $("sector").addEventListener("input", () => {
    updateIndustryOptions();
    apply();
  });

  ["industry","theme","horizon","minCap","minPrice","maxPrice","minRvol","minRS","minRev","minRoic","minWT","minMF","minInd","minRevision","requireCatalyst","sortKey","sortDir"].forEach((id) => {
    $(id).addEventListener("input", apply);
  });

  $("resetBtn").onclick = resetFilters;
  $("saveBtn").onclick = saveFilters;
  $("restoreBtn").onclick = restoreFilters;
  $("exportBtn").onclick = exportCsv;
  $("instantResearchBtn").onclick = runInstantResearch;
  $("closeDetail").onclick = () => { $("detailPanel").hidden = true; };
}

function renderAll() {
  const u = DB.universeStats || {};
  $("metrics").innerHTML = [
    metric("Total Discovered", u.totalDiscovered ?? "—"),
    metric("Eligible Common", u.eligibleCommonStocks ?? "—"),
    metric("Deep Attempted", u.deepAttempted ?? "—"),
    metric("Deep Processed", u.successfullyProcessed ?? "—"),
    metric("True Coverage", Number(u.coveragePct || 0).toFixed(1) + "%"),
    metric("Failures", u.scanFailures ?? "—")
  ].join("");

  const g = DB.dataGovernance || {};
  const ai = DB.aiResearchStatus || {};
  $("governance").innerHTML =
    "<div><b>Deep Scan Scope</b><p>" + esc(g.deepScanScope || "—") + "</p></div>" +
    "<div><b>Market Source</b><p>" + esc(g.marketSource || "—") + "</p></div>" +
    "<div><b>Analyst Revision</b><p>" + esc(g.revisionSource || "資料不足") + "</p></div>" +
    "<div><b>News / Catalyst</b><p>" + esc((g.newsSource || "資料不足") + " · SEC filings") + "</p></div>" +
    "<div><b>Fundamental Source</b><p>" + esc(g.fundamentalSource || "—") + "</p></div>" +
    "<div><b>AI Alpha Research</b><p>" + esc((ai.provider || "No AI secret configured") + (ai.model ? " · " + ai.model : "") + " · fallback: " + (ai.fallback || "evidence synthesis")) + "</p></div>" +
    "<div><b>History Cache</b><p>" + esc(g.historyCache || "—") + "</p></div>" +
    "<div><b>Research Guardrail</b><p>AI 只可使用本次 Market / SEC / Nasdaq evidence；缺資料必須標示資料不足。</p></div>";

  fillSelect("sector", DB.catalog?.sectors || []);
  fillSelect("theme", DB.catalog?.themes || []);
  updateIndustryOptions();
  renderTabs();
  bindEvents();
  apply();
}

boot();
