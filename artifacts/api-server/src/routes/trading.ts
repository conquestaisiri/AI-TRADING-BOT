/**
 * Trading Routes — Market data, bot control, activity streaming
 *
 * Market data:   OKX public REST API (globally accessible, no auth needed)
 *                Falls back to KuCoin if OKX unavailable
 * Bot control:   Spawns actual Python crypto_bot process (uses .venv)
 * Activity:      Real stdout/stderr from bot, parsed + emitted over SSE
 * All-pairs:     Fetches full instrument list from OKX for pair selection
 */

import { Router } from "express";
import { spawn, type ChildProcess } from "child_process";
import path from "path";
import { readFileSync, existsSync, mkdirSync } from "fs";

// pnpm runs scripts in the package directory (artifacts/api-server), so go up 2 levels to workspace root
const WORKSPACE_ROOT = path.resolve(process.cwd(), "../..");

const router = Router();

// ── Activity bus ──────────────────────────────────────────────────────────────
export const activityBus: { listeners: ((msg: string) => void)[] } = {
  listeners: [],
};
export function emit(msg: string) {
  activityBus.listeners.forEach((fn) => fn(msg));
}

// ── Market data sources ───────────────────────────────────────────────────────
const OKX_BASE = "https://www.okx.com/api/v5";
const KUCOIN_BASE = "https://api.kucoin.com/api/v1";

function toOKX(pair: string): string {
  const quote = pair.endsWith("USDT") ? "USDT" : pair.endsWith("BTC") ? "BTC" : pair.endsWith("ETH") ? "ETH" : "USDT";
  return `${pair.slice(0, pair.length - quote.length)}-${quote}`;
}
function toKuCoin(pair: string): string { return toOKX(pair); }

async function apiFetch<T>(url: string, timeout = 8000): Promise<T> {
  const res = await fetch(url, {
    headers: { "Accept": "application/json", "User-Agent": "NeuralTrader/1.0" },
    signal: AbortSignal.timeout(timeout),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.json() as Promise<T>;
}

type OKXCandle = [string, string, string, string, string, string, string, string, string];
type OKXTicker = { last: string; open24h: string; high24h: string; low24h: string; vol24h: string; volCcy24h: string };

async function fetchOKXMarket(pair: string) {
  const inst = toOKX(pair);
  const [tickerRes, candleRes] = await Promise.all([
    apiFetch<{ code: string; data: OKXTicker[] }>(`${OKX_BASE}/market/ticker?instId=${inst}`),
    apiFetch<{ code: string; data: OKXCandle[] }>(`${OKX_BASE}/market/candles?instId=${inst}&bar=15m&limit=120`),
  ]);
  if (tickerRes.code !== "0") throw new Error(`OKX ticker error: ${tickerRes.code}`);
  if (candleRes.code !== "0") throw new Error(`OKX candle error: ${candleRes.code}`);
  const t = tickerRes.data[0];
  const last = parseFloat(t.last);
  const open24h = parseFloat(t.open24h);
  const change24h = open24h > 0 ? ((last - open24h) / open24h) * 100 : 0;
  const candles = candleRes.data.slice().reverse().map((c) => ({
    time: new Date(parseInt(c[0])).toISOString(),
    open: parseFloat(c[1]), high: parseFloat(c[2]),
    low: parseFloat(c[3]), close: parseFloat(c[4]), volume: parseFloat(c[5]),
  }));
  return { price: last, change24h: +change24h.toFixed(2), high24h: parseFloat(t.high24h), low24h: parseFloat(t.low24h), volume24h: parseFloat(t.volCcy24h), candles };
}

type KuCoinCandle = [string, string, string, string, string, string, string];

async function fetchKuCoinMarket(pair: string) {
  const sym = toKuCoin(pair);
  const now = Math.floor(Date.now() / 1000);
  const start = now - 120 * 15 * 60;
  const [statsRes, candleRes] = await Promise.all([
    apiFetch<{ code: string; data: { last: string; changeRate: string; high: string; low: string; volValue: string } }>(`${KUCOIN_BASE}/market/stats?symbol=${sym}`),
    apiFetch<{ code: string; data: KuCoinCandle[] }>(`${KUCOIN_BASE}/market/candles?type=15min&symbol=${sym}&startAt=${start}&endAt=${now}`),
  ]);
  const d = statsRes.data;
  const candles = (candleRes.data ?? []).slice().reverse().map((c) => ({
    time: new Date(parseInt(c[0]) * 1000).toISOString(),
    open: parseFloat(c[1]), close: parseFloat(c[2]), high: parseFloat(c[3]),
    low: parseFloat(c[4]), volume: parseFloat(c[5]),
  }));
  return { price: parseFloat(d.last), change24h: +(parseFloat(d.changeRate) * 100).toFixed(2), high24h: parseFloat(d.high), low24h: parseFloat(d.low), volume24h: parseFloat(d.volValue), candles };
}

function computeEMA(closes: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const out: number[] = [];
  let ema = closes[0] ?? 0;
  for (const c of closes) { ema = c * k + ema * (1 - k); out.push(+ema.toFixed(4)); }
  return out;
}

function computeRSI(closes: number[], period = 14): number[] {
  if (closes.length <= period) return closes.map(() => 50);
  const out: number[] = Array(period).fill(50);
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) avgGain += diff / period; else avgLoss += -diff / period;
  }
  for (let i = period; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const g = diff > 0 ? diff : 0, l = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    out.push(+(100 - 100 / (1 + rs)).toFixed(1));
  }
  return out;
}

async function getMarketData(pair: string) {
  let raw: Awaited<ReturnType<typeof fetchOKXMarket>>;
  try { raw = await fetchOKXMarket(pair); }
  catch (e1) {
    try { raw = await fetchKuCoinMarket(pair); }
    catch (e2) { throw new Error(`All market sources failed. OKX: ${e1}. KuCoin: ${e2}`); }
  }
  const closes = raw.candles.map((c) => c.close);
  const ema20 = computeEMA(closes, 20);
  const ema50 = computeEMA(closes, 50);
  const rsiArr = computeRSI(closes, 14);
  const priceHistory = raw.candles.map((c, i) => ({ ...c, ema20: ema20[i], ema50: ema50[i], rsi: rsiArr[i] ?? 50 }));
  return { ...raw, priceHistory };
}

// ── All-pairs from OKX ─────────────────────────────────────────────────────────
type OKXInstrument = { instId: string; baseCcy: string; quoteCcy: string; state: string };
let cachedAllPairs: string[] = [];
let cachedPairsAt = 0;

async function fetchAllPairs(): Promise<string[]> {
  const now = Date.now();
  if (cachedAllPairs.length && now - cachedPairsAt < 5 * 60 * 1000) return cachedAllPairs;
  try {
    const res = await apiFetch<{ code: string; data: OKXInstrument[] }>(
      `${OKX_BASE}/public/instruments?instType=SPOT`, 15000
    );
    if (res.code !== "0") throw new Error();
    cachedAllPairs = res.data
      .filter((i) => i.quoteCcy === "USDT" && i.state === "live")
      .map((i) => `${i.baseCcy}${i.quoteCcy}`)
      .sort();
    cachedPairsAt = now;
    return cachedAllPairs;
  } catch {
    return cachedAllPairs.length ? cachedAllPairs : DEFAULT_PAIRS;
  }
}

// ── Orderbook ──────────────────────────────────────────────────────────────────
type OKXBook = { bids: [string, string, string, string][]; asks: [string, string, string, string][] };

async function getOrderBook(pair: string) {
  const inst = toOKX(pair);
  try {
    const res = await apiFetch<{ code: string; data: OKXBook[] }>(`${OKX_BASE}/market/books?instId=${inst}&sz=16`);
    if (res.code !== "0" || !res.data?.length) throw new Error("OKX book error");
    const book = res.data[0];
    return {
      bids: book.bids.map(([p, q]) => ({ price: parseFloat(p), qty: parseFloat(q) })),
      asks: book.asks.map(([p, q]) => ({ price: parseFloat(p), qty: parseFloat(q) })),
    };
  } catch {
    const sym = toKuCoin(pair);
    const res = await apiFetch<{ code: string; data: { bids: [string, string][]; asks: [string, string][] } }>(
      `${KUCOIN_BASE}/market/orderbook/level2_20?symbol=${sym}`
    );
    return {
      bids: (res.data?.bids ?? []).slice(0, 16).map(([p, q]) => ({ price: parseFloat(p), qty: parseFloat(q) })),
      asks: (res.data?.asks ?? []).slice(0, 16).map(([p, q]) => ({ price: parseFloat(p), qty: parseFloat(q) })),
    };
  }
}

// ── Tickers ─────────────────────────────────────────────────────────────────
type OKXTickerItem = { instId: string; last: string; open24h: string };

async function getAllTickers(pairs: string[]) {
  try {
    const res = await apiFetch<{ code: string; data: OKXTickerItem[] }>(`${OKX_BASE}/market/tickers?instType=SPOT`);
    if (res.code !== "0") throw new Error();
    return pairs.map((p) => {
      const inst = toOKX(p);
      const t = res.data.find((d) => d.instId === inst);
      const price = t ? parseFloat(t.last) : 0;
      const open = t ? parseFloat(t.open24h) : 0;
      const change = open > 0 ? ((price - open) / open) * 100 : 0;
      return { symbol: p, price, change: +change.toFixed(2) };
    });
  } catch {
    return pairs.map((p) => ({ symbol: p, price: 0, change: 0 }));
  }
}

// ── Bot state ──────────────────────────────────────────────────────────────────
interface BotState {
  running: boolean;
  pair: string | null;
  symbols: string[];
  pid: number | null;
  startedAt: string | null;
  cycle: number;
  lastLogLine: string;
  error: string | null;
  hasApiKeys: boolean;
}

function checkApiKeys(): boolean {
  try {
    const rPath = path.resolve(WORKSPACE_ROOT, "crypto_bot/runtime_settings.json");
    if (existsSync(rPath)) {
      const rs = JSON.parse(readFileSync(rPath, "utf-8"));
      if (rs["BINANCE_API_KEY"] && rs["BINANCE_API_SECRET"]) return true;
    }
  } catch {}
  return !!(process.env["BINANCE_API_KEY"] && process.env["BINANCE_API_SECRET"]);
}

const botState: BotState = {
  running: false, pair: null, symbols: ["BTCUSDT", "ETHUSDT"],
  pid: null, startedAt: null, cycle: 0, lastLogLine: "", error: null,
  hasApiKeys: checkApiKeys(),
};

let botProcess: ChildProcess | null = null;

// ── Log parsing ─────────────────────────────────────────────────────────────
const AGENT_MAP: Record<string, string> = {
  app: "System", signal: "Signal Generator", execution: "Order Executor",
  risk: "Risk Manager", monitor: "Position Monitor", indicators: "Indicator Engine",
  market_data: "Data Fetcher", connector: "Exchange", regime: "Regime Classifier",
  trade_store: "Trade Store", backtesting: "Backtester",
  orchestrator: "AI Orchestrator", judge: "Judge Agent", pods: "AI Pods",
  trend_pod: "Trend Pod", structure_pod: "Structure Pod", regime_pod: "Regime Pod",
  risk_pod: "Risk Pod", execution_pod: "Execution Pod",
};

const LEVEL_MAP: Record<string, string> = {
  INFO: "info", DEBUG: "info", WARNING: "warn", WARN: "warn", ERROR: "error", CRITICAL: "error",
};

function parseBotLog(line: string) {
  const m = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] ([\w.]+):\s*(.*)$/);
  if (!m) return null;
  const [, , levelRaw, moduleName, msg] = m;
  const level = LEVEL_MAP[levelRaw] ?? "info";
  const agent = AGENT_MAP[moduleName] ?? moduleName;
  return { level: classifyMsg(msg, level), agent, msg };
}

function classifyMsg(msg: string, base: string): string {
  const m = msg.toLowerCase();
  if (m.includes("new trade") || m.includes("filled") || m.includes("order placed")) return "order";
  if (m.includes("signal approved") || m.includes("breakout detected")) return "signal";
  if (m.includes("pnl") && m.includes("+")) return "profit";
  if (m.includes("stop loss") || (m.includes("closed") && m.includes("-"))) return "loss";
  if (m.includes("error") || m.includes("failed") || m.includes("exception")) return "error";
  if (m.includes("warn") || m.includes("skip") || m.includes("blocked")) return "warn";
  if (m.includes("ai") || m.includes("pod") || m.includes("judge")) return "ai";
  return base;
}

// ── Spawn bot ──────────────────────────────────────────────────────────────────
function startBotProcess(symbols: string[]): void {
  const botDir = path.resolve(WORKSPACE_ROOT, "crypto_bot");
  const venvPython = path.join(botDir, ".venv/bin/python3");
  const systemPython = ["/usr/bin/python3", "/usr/local/bin/python3", "python3"].find(
    (p) => p === "python3" || existsSync(p)
  ) ?? "python3";
  const pythonBin = existsSync(venvPython) ? venvPython : systemPython;

  // Merge runtime settings into env
  let runtimeEnv: Record<string, string> = {};
  try {
    const rPath = path.join(botDir, "runtime_settings.json");
    if (existsSync(rPath)) runtimeEnv = JSON.parse(readFileSync(rPath, "utf-8"));
  } catch {}

  botState.hasApiKeys = checkApiKeys();
  const symbolStr = symbols.join(",");

  const spawnEnv = {
    ...process.env,
    ...runtimeEnv,
    SYMBOLS: symbolStr,
    PYTHONUNBUFFERED: "1",
    PYTHONPATH: botDir,
    PATH: process.env["PATH"] ?? "",
  };

  emit(JSON.stringify({
    type: "log", level: "system", agent: "System",
    msg: `🚀 Starting NeuralTrader | Symbols: ${symbolStr} | Python: ${pythonBin} | API Keys: ${botState.hasApiKeys ? "✓ loaded" : "✗ missing"}`,
    ts: new Date().toISOString(),
  }));

  botProcess = spawn(pythonBin, ["app.py"], {
    cwd: botDir, env: spawnEnv, stdio: ["ignore", "pipe", "pipe"],
  });

  botState.pid = botProcess.pid ?? null;
  botState.running = true;
  botState.pair = symbols[0];
  botState.symbols = symbols;
  botState.startedAt = new Date().toISOString();
  botState.cycle = 0;
  botState.error = null;

  const handleLine = (line: string) => {
    if (!line.trim()) return;
    botState.lastLogLine = line;
    const parsed = parseBotLog(line);
    const cycleMatch = line.match(/CYCLE\s+(\d+)/i);
    if (cycleMatch) botState.cycle = parseInt(cycleMatch[1]);
    emit(JSON.stringify({
      type: "log", level: parsed?.level ?? "info",
      agent: parsed?.agent ?? "System", msg: parsed?.msg ?? line.trim(),
      ts: new Date().toISOString(),
    }));
  };

  let stdoutBuf = "", stderrBuf = "";
  botProcess.stdout?.on("data", (chunk: Buffer) => {
    stdoutBuf += chunk.toString();
    const lines = stdoutBuf.split("\n"); stdoutBuf = lines.pop() ?? "";
    lines.forEach(handleLine);
  });
  botProcess.stderr?.on("data", (chunk: Buffer) => {
    stderrBuf += chunk.toString();
    const lines = stderrBuf.split("\n"); stderrBuf = lines.pop() ?? "";
    lines.forEach((l) => {
      if (!l.trim()) return;
      const parsed = parseBotLog(l);
      emit(JSON.stringify({ type: "log", level: parsed?.level ?? "warn", agent: parsed?.agent ?? "System", msg: parsed?.msg ?? l.trim(), ts: new Date().toISOString() }));
    });
  });
  botProcess.on("error", (err) => {
    botState.running = false; botState.error = err.message;
    emit(JSON.stringify({ type: "log", level: "error", agent: "System", msg: `❌ Process error: ${err.message}`, ts: new Date().toISOString() }));
  });
  botProcess.on("exit", (code, signal) => {
    botState.running = false; botState.pid = null;
    const reason = signal ? `signal ${signal}` : `exit code ${code}`;
    emit(JSON.stringify({ type: "log", level: code === 0 ? "info" : "warn", agent: "System", msg: `⏹ Bot exited (${reason})`, ts: new Date().toISOString() }));
  });
}

// ── Static default pairs ───────────────────────────────────────────────────────
const DEFAULT_PAIRS = [
  "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
  "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
  "MATICUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
  "FTMUSDT", "AAVEUSDT", "SHIBUSDT", "TRXUSDT", "ALGOUSDT",
];

// ── Trade data ─────────────────────────────────────────────────────────────────
const DB_PATH = path.resolve(WORKSPACE_ROOT, "crypto_bot/storage/trades.db");
const CSV_PATH = path.resolve(WORKSPACE_ROOT, "crypto_bot/storage/closed_trades.csv");

let _sqlite: unknown = null;
async function getSQLite() {
  if (!_sqlite) {
    process.removeAllListeners("warning");
    _sqlite = await import("node:sqlite" as never);
  }
  return _sqlite as { DatabaseSync: new (p: string) => { prepare: (s: string) => { all: () => unknown[] }; close: () => void } };
}

async function readOpenTrades(): Promise<unknown[]> {
  try {
    if (!existsSync(DB_PATH)) return [];
    const { DatabaseSync } = await getSQLite();
    const db = new DatabaseSync(DB_PATH);
    const rows = db.prepare("SELECT * FROM open_trades ORDER BY opened_at DESC LIMIT 50").all();
    db.close(); return rows;
  } catch { return []; }
}

function readClosedTrades(): unknown[] {
  try {
    if (!existsSync(CSV_PATH)) return [];
    const raw = readFileSync(CSV_PATH, "utf-8").trim();
    if (!raw) return [];
    const lines = raw.split("\n");
    if (lines.length < 2) return [];
    const headers = lines[0].split(",");
    return lines.slice(1).slice(-100).map((l) => {
      const vals = l.split(",");
      const obj: Record<string, string> = {};
      headers.forEach((h, i) => { obj[h.trim()] = (vals[i] ?? "").trim(); });
      return obj;
    }).reverse();
  } catch { return []; }
}

// ── Routes ────────────────────────────────────────────────────────────────────

router.get("/pairs", (_req, res) => res.json(DEFAULT_PAIRS));

router.get("/all-pairs", async (_req, res) => {
  try {
    const pairs = await fetchAllPairs();
    res.json(pairs);
  } catch (err) {
    res.json(DEFAULT_PAIRS);
  }
});

router.get("/status", (_req, res) => {
  botState.hasApiKeys = checkApiKeys();
  res.json(botState);
});

router.post("/start", (req, res) => {
  const body = req.body as { pair?: string; symbols?: string[] };
  const symbols = body.symbols?.length ? body.symbols : body.pair ? [body.pair] : ["BTCUSDT"];

  if (botState.running) return res.status(409).json({ error: "Bot already running" });

  botState.hasApiKeys = checkApiKeys();
  if (!botState.hasApiKeys) {
    emit(JSON.stringify({
      type: "log", level: "warn", agent: "System",
      msg: "⚠️ BINANCE_API_KEY + BINANCE_API_SECRET missing. Go to Settings to add them. Bot will fail on exchange connect.",
      ts: new Date().toISOString(),
    }));
  }

  startBotProcess(symbols);
  res.json({ ok: true, symbols, pid: botState.pid });
});

router.post("/stop", (_req, res) => {
  if (botProcess && botState.running) {
    botProcess.kill("SIGTERM");
    setTimeout(() => { if (botState.running && botProcess) botProcess.kill("SIGKILL"); }, 5000);
  }
  botState.running = false; botState.pair = null; botState.pid = null;
  emit(JSON.stringify({ type: "log", level: "warn", agent: "System", msg: "⏹ NeuralTrader halted by user", ts: new Date().toISOString() }));
  res.json({ ok: true });
});

router.get("/market/:pair", async (req, res) => {
  try { res.json(await getMarketData(req.params.pair.toUpperCase())); }
  catch (err) { res.status(502).json({ error: String(err) }); }
});

router.get("/tickers", async (req, res) => {
  const pairsParam = req.query["pairs"] as string | undefined;
  const pairs = pairsParam ? pairsParam.split(",").map((p) => p.toUpperCase()) : DEFAULT_PAIRS;
  try { res.json(await getAllTickers(pairs)); }
  catch (err) { res.status(502).json({ error: String(err) }); }
});

router.get("/orderbook/:pair", async (req, res) => {
  try { res.json(await getOrderBook(req.params.pair.toUpperCase())); }
  catch (err) { res.status(502).json({ error: String(err) }); }
});

router.get("/trades", async (_req, res) => {
  const [open, closed] = await Promise.all([readOpenTrades(), Promise.resolve(readClosedTrades())]);
  res.json({ open, closed });
});

export default router;
