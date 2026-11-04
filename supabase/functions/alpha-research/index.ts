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
import { createClient } from "npm:@supabase/supabase-js@2.95.0";

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


function decodeJwtPayload(token: string): Record<string, unknown> {
  const part = token.split(".")[1];
  if (!part) return {};
  const normalized = part.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  try {
    return JSON.parse(atob(padded));
  } catch {
    return {};
  }
}

function adminClient() {
  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  let secretKey = "";
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") ?? "{}");
    secretKey = keys.default ?? Object.values(keys)[0] ?? "";
  } catch {}
  secretKey = secretKey || Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  if (!supabaseUrl || !secretKey) {
    throw new Error("Supabase admin credentials unavailable");
  }
  return createClient(supabaseUrl, secretKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

async function enforceRateLimit(req: Request, symbol: string) {
  const authHeader = req.headers.get("Authorization") ?? "";
  const token = authHeader.replace(/^Bearer\s+/i, "");
  const claims = decodeJwtPayload(token);
  const userId = String(claims.sub ?? "");
  if (!userId) {
    throw new Error("Authenticated user id unavailable");
  }

  const admin = adminClient();
  const hourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const cooldownAgo = new Date(Date.now() - 30 * 1000).toISOString();

  const { count, error: countError } = await admin
    .from("ai_research_rate_limits")
    .select("id", { count: "exact", head: true })
    .eq("user_id", userId)
    .gte("created_at", hourAgo);

  if (countError) throw countError;
  if ((count ?? 0) >= 10) {
    return { ok: false, status: 429, message: "即時 AI 已達每小時 10 次上限" };
  }

  const { count: recentCount, error: recentError } = await admin
    .from("ai_research_rate_limits")
    .select("id", { count: "exact", head: true })
    .eq("user_id", userId)
    .gte("created_at", cooldownAgo);

  if (recentError) throw recentError;
  if ((recentCount ?? 0) >= 1) {
    return { ok: false, status: 429, message: "請稍候 30 秒再重新生成" };
  }

  const { data: inserted, error: insertError } = await admin
    .from("ai_research_rate_limits")
    .insert({ user_id: userId, symbol })
    .select("id")
    .single();

  if (insertError) throw insertError;

  const cleanupBefore = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
  await admin
    .from("ai_research_rate_limits")
    .delete()
    .lt("created_at", cleanupBefore);

  return { ok: true, status: 200, message: "ok", reservationId: inserted?.id ?? null };
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

  let reservationId: number | null = null;
  try {
    const limit = await enforceRateLimit(req, String(evidence.symbol));
    if (!limit.ok) {
      return json({ error: limit.message }, limit.status, origin);
    }
    reservationId = limit.reservationId ?? null;
  } catch (error) {
    return json({
      error: "Rate limit verification failed",
      detail: String(error).slice(0, 220),
    }, 500, origin);
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

  async function callGemini(model: string) {
    const url =
      `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`;

    const body = JSON.stringify({
      contents: [{ role: "user", parts: [{ text: prompt }] }],
      generationConfig: {
        maxOutputTokens: 3200,
        thinkingConfig: { thinkingLevel: "minimal" },
      },
    });

    let lastError: unknown = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        const ai = await fetch(url, {
          method: "POST",
          headers: {
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json",
          },
          body,
        });

        const payload = await ai.json();

        if ([429, 500, 502, 503, 504].includes(ai.status)) {
          lastError = new Error(
            "Gemini transient HTTP " + ai.status + ": " +
            (payload?.error?.message ?? "unknown")
          );
          await new Promise((resolve) => setTimeout(resolve, 1200 * (attempt + 1)));
          continue;
        }

        if (!ai.ok) {
          throw new Error(
            "Gemini HTTP " + ai.status + ": " +
            (payload?.error?.message ?? "unknown")
          );
        }

        const parts = payload?.candidates?.[0]?.content?.parts ?? [];
        const report = parts
          .filter((p: { thought?: boolean }) => !p.thought)
          .map((p: { text?: string }) => p.text ?? "")
          .join("\n")
          .trim();

        if (!report) throw new Error("Gemini returned empty output");
        return { report, model };
      } catch (error) {
        lastError = error;
        if (attempt < 3) {
          await new Promise((resolve) => setTimeout(resolve, 1200 * (attempt + 1)));
        }
      }
    }

    throw lastError ?? new Error("Gemini request failed");
  }

  try {
    let result;
    try {
      result = await callGemini(GEMINI_MODEL);
    } catch (primaryError) {
      if (GEMINI_MODEL === "gemini-3.5-flash-lite") throw primaryError;
      result = await callGemini("gemini-3.5-flash-lite");
    }

    return json({
      report: result.report,
      model: result.model,
      grounded: true,
      generatedAt: new Date().toISOString(),
    }, 200, origin);
  } catch (error) {
    if (reservationId !== null) {
      try {
        await adminClient()
          .from("ai_research_rate_limits")
          .delete()
          .eq("id", reservationId);
      } catch {}
    }

    return json({
      error: "AI gateway failure",
      detail: String(error).slice(0, 240),
    }, 502, origin);
  }
});
