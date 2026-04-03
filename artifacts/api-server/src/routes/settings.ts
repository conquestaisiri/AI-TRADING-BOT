/**
 * Settings Routes — API key and runtime configuration management
 * Stores settings in crypto_bot/runtime_settings.json
 * Keys are masked on GET but fully written on POST
 */

import { Router, type Request, type Response } from "express";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import path from "path";
import { activityBus } from "./trading.js";

// Use process.cwd() — always resolves to workspace root regardless of build layout
const SETTINGS_PATH = path.resolve(process.cwd(), "crypto_bot/runtime_settings.json");

const router = Router();

const SETTING_KEYS = [
  "BINANCE_API_KEY",
  "BINANCE_API_SECRET",
  "OPENROUTER_API_KEY",
  "GROQ_API_KEY",
  "OLLAMA_BASE_URL",
  "OLLAMA_API_KEY",
  "SYMBOLS",
  "RISK_PERCENT",
  "REWARD_TO_RISK",
  "AI_ENABLED",
  "AI_MODE",
  "AI_ORCHESTRATION_MODE",
  "AI_DEFAULT_PROVIDER",
  "AI_DEFAULT_MODEL",
  "AI_MIN_SCORE_FOR_REVIEW",
  "AI_MIN_SCORE_FOR_EXECUTION",
  "AI_REQUIRE_JUDGE_APPROVAL",
  "AI_ALLOW_RULE_ONLY_FALLBACK",
  "TREND_POD_MODEL_A",
  "TREND_POD_MODEL_B",
  "STRUCTURE_POD_MODEL_A",
  "STRUCTURE_POD_MODEL_B",
  "REGIME_POD_MODEL_A",
  "REGIME_POD_MODEL_B",
  "RISK_POD_MODEL_A",
  "RISK_POD_MODEL_B",
  "EXECUTION_POD_MODEL_A",
  "EXECUTION_POD_MODEL_B",
  "JUDGE_AGENT_MODEL",
  "JUDGE_AGENT_PROVIDER",
  "VOLUME_RATIO_THRESHOLD",
  "REGIME_MIN_TREND_SCORE",
  "LOSS_COOLDOWN_CANDLES",
  "WIN_COOLDOWN_CANDLES",
  "MAX_TRADES_PER_WINDOW",
];

const SENSITIVE_KEYS = new Set([
  "BINANCE_API_KEY",
  "BINANCE_API_SECRET",
  "OPENROUTER_API_KEY",
  "GROQ_API_KEY",
  "OLLAMA_API_KEY",
]);

function loadRuntimeSettings(): Record<string, string> {
  if (!existsSync(SETTINGS_PATH)) return {};
  try {
    return JSON.parse(readFileSync(SETTINGS_PATH, "utf-8"));
  } catch {
    return {};
  }
}

function saveRuntimeSettings(settings: Record<string, string>): void {
  writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2), "utf-8");
}

function maskValue(key: string, value: string): string {
  if (!SENSITIVE_KEYS.has(key) || !value) return value;
  if (value.length <= 8) return "••••••••";
  return "••••••••" + value.slice(-4);
}

function emit(msg: object) {
  const str = JSON.stringify(msg);
  activityBus.listeners.forEach((fn) => fn(str));
}

router.get("/", (_req: Request, res: Response) => {
  const runtime = loadRuntimeSettings();
  const merged: Record<string, string> = {};
  const masked: Record<string, string> = {};
  const sources: Record<string, "env" | "runtime" | "default"> = {};

  for (const key of SETTING_KEYS) {
    const runtimeVal = runtime[key] ?? "";
    const envVal = process.env[key] ?? "";
    const value = runtimeVal || envVal;
    merged[key] = value;
    masked[key] = maskValue(key, value);
    sources[key] = runtimeVal ? "runtime" : envVal ? "env" : "default";
  }

  const hasApiKeys = !!(
    (merged["BINANCE_API_KEY"] && merged["BINANCE_API_SECRET"])
  );
  const hasOpenRouter = !!merged["OPENROUTER_API_KEY"];
  const hasGroq = !!merged["GROQ_API_KEY"];
  const hasOllama = !!merged["OLLAMA_BASE_URL"];

  res.json({
    settings: masked,
    sources,
    health: {
      binance: hasApiKeys,
      openRouter: hasOpenRouter,
      groq: hasGroq,
      ollama: hasOllama,
      aiReady: hasOpenRouter || hasGroq || hasOllama,
    },
  });
});

router.post("/", (req: Request, res: Response) => {
  const body = req.body as Record<string, string>;
  if (!body || typeof body !== "object") {
    return res.status(400).json({ error: "Invalid request body" });
  }

  const runtime = loadRuntimeSettings();
  const updated: string[] = [];

  for (const key of SETTING_KEYS) {
    if (key in body) {
      const val = String(body[key] ?? "").trim();
      if (val === "" && SENSITIVE_KEYS.has(key)) {
        // empty sensitive key means "keep existing"
        continue;
      }
      runtime[key] = val;
      updated.push(key);
    }
  }

  saveRuntimeSettings(runtime);

  emit({
    type: "log",
    level: "system",
    agent: "Settings",
    msg: `⚙️  Settings updated: ${updated.join(", ")}`,
    ts: new Date().toISOString(),
  });

  res.json({ ok: true, updated });
});

export { loadRuntimeSettings };
export default router;
