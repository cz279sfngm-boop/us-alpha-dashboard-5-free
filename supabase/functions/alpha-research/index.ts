// Supabase Edge Function: alpha-research
// Purpose: secure on-demand Gemini synthesis for already-grounded stock evidence.
// Deploy with verify_jwt=true. The browser must call this function with a valid
// Supabase user JWT; anonymous sign-in is sufficient for a personal dashboard.
//
// Required Supabase secret:
//   GEMINI_API_KEY
// Optional:
//   GEMINI_MODEL (defaults to gemini-3.6-flash)
//   ALLOWED_ORIGIN (defaults to the GitHub Pages production origin)

import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const ALLOWED_ORIGIN =
  Deno.env.get("ALLOWED_ORIGIN") ??
  "https://cz279sfngm-boop.github.io";

const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY") ?? "";
const GEMINI_MODEL = Deno.env.get("GEMINI_MODEL") ?? "gemini-3.6-flash";

const cors = (origin: string | null) => ({
  "Access-Control-Allow-Origin": origin === ALLOWED_ORIGIN ? origin : ALLOWED_ORIGIN,
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Vary": "Origin",
});

const json = (body: unknown, status = 200, origin: string | null = null) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      ...cors(origin),
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });

function trimEvidence(input: Record<string, unknown>) {
  const allowed = [
    "symbol","name","sector","industry","themes","price","marketCap",
    "sScore","mScore","gScore","dScore","rs20","rvol","rsi14","atr14",
    "return1m","return3m","return6m","return1y",
    "waveTrendScore","waveTrendState","mfScore","mfState","mfDivergence",
    "indicatorScore","revenueGrowth","epsGrowth","fcfMargin","operatingMargin",
    "grossMargin","netMargin","roe","roic","cash","debt",
    "revisionSource","revisionConfidence","revisionScore","revisionUp4w",
    "revisionDown4w","revisionBreadth4wPct","nextQuarterForecast",
    "nextYearForecast","news","recentFilings","forwardPE","riskScore",
    "trueAlphaScore","momentumScore","dataConfidence"
  ];
  return Object.fromEntries(allowed.map((k) => [k, input[k]]));
}

Deno.serve(async (req: Request) => {
  const origin = req.headers.get("origin");

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors(origin) });
  }
  if (req.method !== "POST") {
    return json({ error: "POST required" }, 405, origin);
  }
  if (origin && origin !== ALLOWED_ORIGIN) {
    return json({ error: "Origin not allowed" }, 403, origin);
  }
  if (!GEMINI_API_KEY) {
    return json({ error: "AI gateway is not configured" }, 503, origin);
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return json({ error: "Invalid JSON" }, 400, origin);
  }

  const evidence =
    body && typeof body.evidence === "object" && body.evidence
      ? trimEvidence(body.evidence as Record<string, unknown>)
      : null;

  if (!evidence || !evidence.symbol) {
    return json({ error: "Grounded evidence is required" }, 400, origin);
  }

  const prompt = `你是機構級美股研究助理。只允許使用 EVIDENCE JSON。
禁止使用模型記憶補充任何公司事實；缺資料必須寫「資料不足」。
嚴格證據規則：
- [NEWS-n] 只有標題/摘要層級證據。任何由 NEWS 得出的敘述必須寫成「Nasdaq 收錄的 <publisher> 報導/摘要稱……」，不得改寫成已獨立證實的公司事實。
- [FIL-n] 只證明 Form、日期、期間與連結存在，禁止推測 filing 內容。
- [REV] 只證明共識 EPS、估計數量及過去4週 Up/Down revisions，不代表實際業績。
- 不得因為有新聞或有 filing 本身就判斷利多/利空。
引用標籤：Market=[MKT]、SEC/financial=[SEC]、Nasdaq analyst=[REV]、
news=[NEWS-n]、regulatory filing=[FIL-n]。

請以繁體中文輸出：
1. 執行摘要
2. 商業模式與收入驅動
3. 產業需求與供應鏈
4. 盈利預期修正
5. 財務質量
6. 估值
7. 技術與資金
8. 最新催化劑
9. 主要風險 / 反證
10. 證據缺口
最後寫「此報告不是投資建議。」

EVIDENCE JSON:
${JSON.stringify(evidence)}`;

  try {
    const url =
      `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(GEMINI_MODEL)}:generateContent`;

    const ai = await fetch(url, {
      method: "POST",
      headers: {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        contents: [{ role: "user", parts: [{ text: prompt }] }],
        generationConfig: {
          maxOutputTokens: 3200,
          thinkingConfig: { thinkingLevel: "minimal" },
        },
      }),
    });

    const payload = await ai.json();

    if (!ai.ok) {
      return json({
        error: "AI provider error",
        status: ai.status,
        detail: payload?.error?.message ?? "unknown",
      }, 502, origin);
    }

    const parts = payload?.candidates?.[0]?.content?.parts ?? [];
    const report = parts.map((p: { text?: string }) => p.text ?? "").join("\n").trim();

    if (!report) {
      return json({ error: "AI returned empty output" }, 502, origin);
    }

    return json({
      report,
      model: GEMINI_MODEL,
      grounded: true,
      generatedAt: new Date().toISOString(),
    }, 200, origin);
  } catch (error) {
    return json({
      error: "AI gateway failure",
      detail: String(error).slice(0, 240),
    }, 500, origin);
  }
});
