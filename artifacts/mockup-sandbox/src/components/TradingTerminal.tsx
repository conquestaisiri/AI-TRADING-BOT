/**
 * NeuralTrader — AI-Powered Crypto Trading Terminal
 *
 * Real data sources:
 *  - Market data (OHLCV, tickers, orderbook) → Binance public REST API via /api/bot
 *  - Bot activity → actual Python process stdout streamed over SSE /api/activity/stream
 *  - Bot control → /api/bot/start, /api/bot/stop
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// API is at /api on the same Replit proxy host
const API = "/api";

// ─── Types ────────────────────────────────────────────────────────────────────

interface BotStatus {
  running: boolean;
  pair: string | null;
  pid: number | null;
  startedAt: string | null;
  cycle: number;
  lastLogLine: string;
  error: string | null;
  hasApiKeys: boolean;
}

interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema20: number;
  ema50: number;
  rsi: number;
  // computed for chart layout
  bullish?: boolean;
  bodyTop?: number;
  bodyBot?: number;
  wickTop?: number;
  wickBot?: number;
}

interface MarketData {
  pair: string;
  price: number;
  change24h: number;
  high24h: number;
  low24h: number;
  volume24h: number;
  priceHistory: Candle[];
}

interface TickerItem {
  symbol: string;
  price: number;
  change: number;
}

interface OrderBookEntry {
  price: number;
  qty: number;
}

interface OrderBook {
  bids: OrderBookEntry[];
  asks: OrderBookEntry[];
}

interface ActivityEntry {
  id: string;
  type: string;
  level: string;
  agent: string;
  msg: string;
  ts: string;
}

interface AgentStage {
  stage: number;
  name: string;
  icon: string;
  status: "idle" | "active" | "done" | "skip" | "warn" | "error";
  detail: string;
  calls: number;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT"];

const DEFAULT_AGENTS: AgentStage[] = [
  { stage: 1, name: "Market Analyst",     icon: "🔍", status: "idle", detail: "Awaiting launch", calls: 0 },
  { stage: 2, name: "Data Fetcher",       icon: "📡", status: "idle", detail: "Standby",         calls: 0 },
  { stage: 3, name: "Indicator Engine",   icon: "📊", status: "idle", detail: "Standby",         calls: 0 },
  { stage: 4, name: "Regime Classifier",  icon: "🧭", status: "idle", detail: "Standby",         calls: 0 },
  { stage: 5, name: "Signal Generator",   icon: "⚡", status: "idle", detail: "Standby",         calls: 0 },
  { stage: 6, name: "Risk Manager",       icon: "⚖️", status: "idle", detail: "Standby",         calls: 0 },
  { stage: 7, name: "Order Executor",     icon: "🎯", status: "idle", detail: "Standby",         calls: 0 },
];

const LEVEL_COLOR: Record<string, string> = {
  system:  "#a78bfa",
  info:    "#64748b",
  warn:    "#f59e0b",
  error:   "#ef4444",
  signal:  "#22d3ee",
  order:   "#34d399",
  profit:  "#10b981",
  loss:    "#f87171",
};

const AGENT_STAGE: Record<string, number> = {
  "Market Analyst":     1,
  "System":             0,
  "Data Fetcher":       2,
  "Indicator Engine":   3,
  "Regime Classifier":  4,
  "Signal Generator":   5,
  "Risk Manager":       6,
  "Order Executor":     7,
  "Position Monitor":   1,
  "Exchange":           0,
  "Trade Store":        0,
};

function stageFromAgent(agent: string): number {
  return AGENT_STAGE[agent] ?? 0;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmt(n: number, d = 2) {
  return n?.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) ?? "—";
}
function fmtK(n: number) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return fmt(n);
}
function fmtTime(ts: string) {
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}
function fmtShortTime(ts: string) {
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}
function fmtDuration(from: string) {
  const secs = Math.floor((Date.now() - new Date(from).getTime()) / 1000);
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function inferRegime(candles: Candle[]): { label: string; score: number; color: string } {
  if (candles.length < 20) return { label: "UNKNOWN", score: 0, color: "#6b7280" };
  const last = candles[candles.length - 1];
  const emaSpread = Math.abs((last.ema20 - last.ema50) / last.ema50);
  const score = Math.min(1, emaSpread * 50);
  if (score > 0.6) return { label: "TRENDING", score: +score.toFixed(2), color: "#10b981" };
  if (score > 0.3) return { label: "RANGING",  score: +score.toFixed(2), color: "#f59e0b" };
  return { label: "CHOPPY", score: +score.toFixed(2), color: "#ef4444" };
}

// ─── Custom Candlestick (SVG via recharts custom shape) ───────────────────────

interface CandleProps {
  x?: number; y?: number; width?: number; height?: number;
  payload?: Candle & { _min?: number; _max?: number };
  value?: number[];
}

function CandleShape({ x = 0, y = 0, width = 0, height = 0, payload }: CandleProps) {
  if (!payload || !height) return null;
  const { open, high, low, close } = payload;
  const bull = close >= open;
  const fill = bull ? "#10b981" : "#f87171";
  const stroke = bull ? "#059669" : "#dc2626";

  const priceMin = payload._min ?? Math.min(low, open, close);
  const priceMax = payload._max ?? Math.max(high, open, close);
  const range = priceMax - priceMin || 1;
  const toY = (v: number) => y + ((priceMax - v) / range) * height;

  const bodyT = toY(Math.max(open, close));
  const bodyB = toY(Math.min(open, close));
  const bodyH = Math.max(1, bodyB - bodyT);
  const wickT = toY(high);
  const wickB = toY(low);
  const cx = x + width / 2;

  return (
    <g>
      <line x1={cx} y1={wickT} x2={cx} y2={bodyT} stroke={stroke} strokeWidth={1} />
      <line x1={cx} y1={bodyB} x2={cx} y2={wickB} stroke={stroke} strokeWidth={1} />
      <rect x={x + 1} y={bodyT} width={Math.max(1, width - 2)} height={bodyH} fill={fill} stroke={stroke} strokeWidth={0.5} />
    </g>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function TradingTerminal() {
  const [selectedPair, setSelectedPair] = useState("BTCUSDT");
  const [status, setStatus]             = useState<BotStatus | null>(null);
  const [market, setMarket]             = useState<MarketData | null>(null);
  const [tickers, setTickers]           = useState<TickerItem[]>([]);
  const [orderBook, setOrderBook]       = useState<OrderBook | null>(null);
  const [activity, setActivity]         = useState<ActivityEntry[]>([]);
  const [agents, setAgents]             = useState<AgentStage[]>(DEFAULT_AGENTS);
  const [activeTab, setActiveTab]       = useState<"book" | "activity" | "equity">("activity");
  const [bottomTab, setBottomTab]       = useState<"positions" | "history" | "metrics">("positions");
  const [positions, setPositions]       = useState<Record<string, string>[]>([]);
  const [closedTrades, setClosedTrades] = useState<Record<string, string>[]>([]);
  const [serverTime, setServerTime]     = useState(new Date());
  const [isStarting, setIsStarting]     = useState(false);
  const [equityHistory]                 = useState(() => {
    const base = 10000;
    return Array.from({ length: 60 }, (_, i) => ({
      t: i,
      equity: base + (Math.random() - 0.48) * 200 * (i / 10),
    }));
  });

  const activityRef = useRef<HTMLDivElement>(null);
  const sseRef      = useRef<EventSource | null>(null);

  // ── Server clock ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const t = setInterval(() => setServerTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // ── SSE activity stream ───────────────────────────────────────────────────────
  useEffect(() => {
    sseRef.current?.close();
    const es = new EventSource(`${API}/activity/stream`);
    sseRef.current = es;

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (!data.msg) return;

        const entry: ActivityEntry = {
          id:    `${Date.now()}-${Math.random()}`,
          type:  data.type ?? "log",
          level: data.level ?? "info",
          agent: data.agent ?? "System",
          msg:   data.msg,
          ts:    data.ts ?? new Date().toISOString(),
        };

        setActivity((prev) => [...prev.slice(-299), entry]);

        // Update agent status based on which agent is logging
        const stageNum = stageFromAgent(entry.agent);
        if (stageNum > 0) {
          setAgents((prev) => prev.map((a) =>
            a.stage === stageNum
              ? { ...a, status: "active", detail: entry.msg.slice(0, 40), calls: a.calls + 1 }
              : a
          ));
          // After a moment, mark done
          setTimeout(() => {
            setAgents((prev) => prev.map((a) =>
              a.stage === stageNum && a.status === "active"
                ? { ...a, status: "done" }
                : a
            ));
          }, 2500);
        }
      } catch { /* ignore parse errors */ }
    };

    es.onerror = () => {};
    return () => es.close();
  }, []);

  // ── Scroll activity to bottom ────────────────────────────────────────────────
  useEffect(() => {
    if (activityRef.current) {
      activityRef.current.scrollTop = activityRef.current.scrollHeight;
    }
  }, [activity]);

  // ── Polling ───────────────────────────────────────────────────────────────────
  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/bot/status`);
      if (r.ok) setStatus(await r.json());
    } catch { /* network errors are silent */ }
  }, []);

  const fetchMarket = useCallback(async (pair: string) => {
    try {
      const r = await fetch(`${API}/bot/market/${pair}`);
      if (r.ok) setMarket(await r.json());
    } catch { /* silent */ }
  }, []);

  const fetchTickers = useCallback(async () => {
    try {
      const r = await fetch(`${API}/bot/tickers`);
      if (r.ok) setTickers(await r.json());
    } catch { /* silent */ }
  }, []);

  const fetchOrderBook = useCallback(async (pair: string) => {
    try {
      const r = await fetch(`${API}/bot/orderbook/${pair}`);
      if (r.ok) setOrderBook(await r.json());
    } catch { /* silent */ }
  }, []);

  const fetchTrades = useCallback(async () => {
    try {
      const r = await fetch(`${API}/bot/trades`);
      if (r.ok) {
        const d = await r.json() as { open: Record<string, string>[]; closed: Record<string, string>[] };
        setPositions(d.open ?? []);
        setClosedTrades(d.closed ?? []);
      }
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchMarket(selectedPair);
    fetchTickers();
    fetchOrderBook(selectedPair);
    fetchTrades();

    const t1 = setInterval(fetchStatus, 3000);
    const t2 = setInterval(() => fetchMarket(selectedPair), 10000);
    const t3 = setInterval(fetchTickers, 15000);
    const t4 = setInterval(() => fetchOrderBook(selectedPair), 5000);
    const t5 = setInterval(fetchTrades, 8000);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); clearInterval(t4); clearInterval(t5); };
  }, [selectedPair, fetchStatus, fetchMarket, fetchTickers, fetchOrderBook, fetchTrades]);

  // ── Handlers ──────────────────────────────────────────────────────────────────
  async function handleStart() {
    if (isStarting || status?.running) return;
    setIsStarting(true);
    try {
      const r = await fetch(`${API}/bot/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair: selectedPair }),
      });
      if (!r.ok) {
        const err = await r.json();
        console.error("Start error:", err);
      }
      await fetchStatus();
      setAgents(DEFAULT_AGENTS.map((a) => ({ ...a, status: "idle", detail: "Initializing...", calls: 0 })));
    } finally {
      setIsStarting(false);
    }
  }

  async function handleStop() {
    await fetch(`${API}/bot/stop`, { method: "POST" });
    await fetchStatus();
    setAgents(DEFAULT_AGENTS.map((a) => ({ ...a, status: "idle", detail: "Stopped" })));
    setPositions([]);
  }

  // ── Derived values ────────────────────────────────────────────────────────────
  const isRunning   = status?.running ?? false;
  const price       = market?.price ?? 0;
  const change24h   = market?.change24h ?? 0;
  const priceColor  = change24h >= 0 ? "#10b981" : "#f87171";
  const regime      = market ? inferRegime(market.priceHistory) : null;

  // Prepare chart data with range for candlestick positioning
  const chartData = (market?.priceHistory ?? []).slice(-100).map((c) => ({
    ...c,
    time: fmtShortTime(c.time),
    bullish: c.close >= c.open,
    _min: 0,
    _max: 0,
  }));
  const priceMin = chartData.length ? Math.min(...chartData.map((c) => c.low)) * 0.9998 : 0;
  const priceMax = chartData.length ? Math.max(...chartData.map((c) => c.high)) * 1.0002 : 1;
  // Inject _min/_max for shape
  chartData.forEach((c) => { c._min = priceMin; c._max = priceMax; });

  const maxBid = orderBook ? Math.max(...orderBook.bids.map((b) => b.qty)) : 1;
  const maxAsk = orderBook ? Math.max(...orderBook.asks.map((a) => a.qty)) : 1;

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div
      className="flex flex-col w-full overflow-hidden select-none"
      style={{ height: "100vh", background: "#070a10", color: "#c8d3e8", fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace" }}
    >
      {/* ═══════════════════ TOP HEADER ══════════════════════════════════════ */}
      <header
        className="flex items-center gap-3 px-4 border-b flex-shrink-0"
        style={{ height: 44, borderColor: "#111827", background: "#0a0d16" }}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 mr-3">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
            <polygon points="12,2 22,19 2,19" fill="#1d4ed8" opacity="0.9"/>
            <polygon points="12,7 18,17 6,17" fill="#3b82f6" opacity="0.6"/>
            <circle cx="12" cy="12" r="2.5" fill="#60a5fa"/>
          </svg>
          <span className="text-xs font-bold tracking-[0.2em]" style={{ color: "#60a5fa" }}>NEURALTRADER</span>
          <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: "#1e3a6e", color: "#93c5fd", fontSize: 9 }}>PRO v2.1</span>
        </div>

        {/* Live ticker strip */}
        <div className="flex gap-1 flex-1 overflow-hidden">
          {tickers.slice(0, 7).map((t) => (
            <button
              key={t.symbol}
              onClick={() => { setSelectedPair(t.symbol); }}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded transition-all"
              style={{
                background: selectedPair === t.symbol ? "#0f1e3a" : "transparent",
                border: `1px solid ${selectedPair === t.symbol ? "#1e3a6e" : "transparent"}`,
                fontSize: 10,
              }}
            >
              <span style={{ color: selectedPair === t.symbol ? "#93c5fd" : "#475569" }}>
                {t.symbol.replace("USDT", "")}
              </span>
              <span className="tabular-nums" style={{ color: t.change >= 0 ? "#10b981" : "#f87171" }}>
                {t.change >= 0 ? "+" : ""}{t.change.toFixed(2)}%
              </span>
            </button>
          ))}
        </div>

        {/* Status strip */}
        <div className="flex items-center gap-4 text-xs ml-auto">
          {regime && (
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full" style={{ background: regime.color }} />
              <span style={{ color: regime.color }}>{regime.label}</span>
              <span style={{ color: "#374151" }}>{regime.score}</span>
            </div>
          )}
          {status?.hasApiKeys === false && (
            <span className="px-2 py-0.5 rounded text-xs" style={{ background: "#451a03", color: "#fbbf24", fontSize: 9 }}>
              ⚠ NO API KEYS
            </span>
          )}
          <span style={{ color: "#374151", fontSize: 10 }}>
            {serverTime.toLocaleTimeString("en-US", { hour12: false })} UTC
          </span>
          <div className="flex items-center gap-1.5">
            <div
              className="w-2 h-2 rounded-full"
              style={{
                background: isRunning ? "#10b981" : "#1f2937",
                boxShadow: isRunning ? "0 0 8px #10b981" : "none",
                animation: isRunning ? "pulse 2s infinite" : "none",
              }}
            />
            <span style={{ color: isRunning ? "#10b981" : "#374151" }}>
              {isRunning ? `LIVE · ${status?.pair} · Cycle ${status?.cycle}` : "STANDBY"}
            </span>
          </div>
        </div>
      </header>

      {/* ═══════════════════ PRICE BAR ═══════════════════════════════════════ */}
      <div
        className="flex items-center gap-6 px-4 border-b flex-shrink-0"
        style={{ height: 52, borderColor: "#111827", background: "#0b0e1a" }}
      >
        {/* Main price */}
        <div className="flex items-baseline gap-3">
          <span
            className="text-2xl font-bold tabular-nums"
            style={{ color: priceColor, textShadow: isRunning ? `0 0 20px ${priceColor}40` : "none" }}
          >
            {price ? `$${fmt(price)}` : "—"}
          </span>
          <div className="flex flex-col text-xs">
            <span style={{ color: priceColor }}>{change24h >= 0 ? "+" : ""}{change24h.toFixed(2)}%</span>
            <span style={{ color: "#374151" }}>24h</span>
          </div>
        </div>

        {/* Market stats */}
        <div className="flex gap-5 text-xs" style={{ color: "#475569", borderLeft: "1px solid #1a2035", paddingLeft: 20 }}>
          {[
            ["24h High", market ? `$${fmt(market.high24h)}` : "—", "#10b981"],
            ["24h Low",  market ? `$${fmt(market.low24h)}`  : "—", "#f87171"],
            ["Volume",   market ? fmtK(market.volume24h)     : "—", "#94a3b8"],
          ].map(([k, v, c]) => (
            <div key={k} className="flex flex-col">
              <span style={{ color: "#374151", fontSize: 9, letterSpacing: "0.05em" }}>{k}</span>
              <span className="tabular-nums font-bold" style={{ color: c }}>{v}</span>
            </div>
          ))}
        </div>

        {/* Pair + Launch */}
        <div className="ml-auto flex items-center gap-2">
          <select
            disabled={isRunning}
            value={selectedPair}
            onChange={(e) => setSelectedPair(e.target.value)}
            className="text-xs px-3 py-1.5 rounded outline-none"
            style={{ background: "#111827", border: "1px solid #1e2d4a", color: "#94a3b8", fontFamily: "inherit" }}
          >
            {PAIRS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>

          {!isRunning ? (
            <button
              onClick={handleStart}
              disabled={isStarting}
              className="px-5 py-1.5 rounded font-bold text-xs transition-all"
              style={{
                background: isStarting ? "#1e2d4a" : "linear-gradient(135deg, #1d4ed8, #2563eb)",
                color: "#fff",
                border: "1px solid #3b82f6",
                boxShadow: isStarting ? "none" : "0 0 16px rgba(59,130,246,0.4)",
                letterSpacing: "0.1em",
                fontFamily: "inherit",
              }}
            >
              {isStarting ? "INITIALIZING…" : "▶ LAUNCH SYSTEM"}
            </button>
          ) : (
            <button
              onClick={handleStop}
              className="px-5 py-1.5 rounded font-bold text-xs"
              style={{
                background: "linear-gradient(135deg, #7f1d1d, #991b1b)",
                color: "#fca5a5",
                border: "1px solid #ef4444",
                letterSpacing: "0.1em",
                fontFamily: "inherit",
              }}
            >
              ⏹ HALT SYSTEM
            </button>
          )}
        </div>
      </div>

      {/* ═══════════════════ MAIN GRID ═══════════════════════════════════════ */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── LEFT PANEL: Agent Pods ─────────────────────────────────────────── */}
        <div
          className="flex flex-col border-r overflow-y-auto flex-shrink-0"
          style={{ width: 206, borderColor: "#111827", background: "#090c14" }}
        >
          <SectionHeader label="AI AGENT PODS" />

          <div className="p-1.5 space-y-1">
            {agents.map((a) => <AgentPod key={a.stage} agent={a} isRunning={isRunning} />)}
          </div>

          <div className="border-t mt-auto" style={{ borderColor: "#111827" }}>
            <SectionHeader label="RISK PARAMETERS" />
            <div className="px-3 pb-3 space-y-1.5">
              {[
                ["Risk/Trade",   "1.00 %"],
                ["Reward:Risk",  "2.0 ×"],
                ["ATR Mult SL",  "1.5 ×"],
                ["Vol Thresh",   "≥ 1.5 × avg"],
                ["Min Regime",   "≥ 0.50"],
                ["Max Open",     "1 / symbol"],
                ["Loop Interval","60 s"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs">
                  <span style={{ color: "#374151" }}>{k}</span>
                  <span style={{ color: "#64748b" }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── CENTER: Chart + Positions ───────────────────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0">

          {/* Price Chart */}
          <div className="flex-1 relative" style={{ minHeight: 0 }}>
            <div
              className="absolute inset-0 p-2"
              style={{ display: "flex", flexDirection: "column" }}
            >
              {/* Chart toolbar */}
              <div className="flex items-center gap-4 mb-1 px-1 flex-shrink-0">
                <span className="text-xs font-bold tracking-widest" style={{ color: "#3b82f6", letterSpacing: "0.15em" }}>
                  {selectedPair} · 15M · CANDLESTICK
                </span>
                <div className="flex items-center gap-3 text-xs">
                  <LegendDot color="#22d3ee" label="EMA 20" />
                  <LegendDot color="#f59e0b" label="EMA 50" />
                  {regime && (
                    <span style={{ color: regime.color, fontSize: 10 }}>
                      ◈ {regime.label} ({regime.score})
                    </span>
                  )}
                </div>
                <div className="ml-auto text-xs" style={{ color: "#374151" }}>
                  {chartData.length} candles · live
                </div>
              </div>

              {/* Recharts chart */}
              <div className="flex-1" style={{ minHeight: 0 }}>
                {chartData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={chartData} margin={{ top: 4, right: 2, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="2 4" stroke="#111827" vertical={false} />
                      <XAxis
                        dataKey="time"
                        tick={{ fontSize: 9, fill: "#374151", fontFamily: "monospace" }}
                        tickLine={false}
                        axisLine={false}
                        interval={Math.floor(chartData.length / 8)}
                      />
                      <YAxis
                        domain={[priceMin, priceMax]}
                        tick={{ fontSize: 9, fill: "#374151", fontFamily: "monospace" }}
                        tickLine={false}
                        axisLine={false}
                        width={72}
                        tickFormatter={(v: number) =>
                          v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v.toFixed(2)}`
                        }
                      />
                      <Tooltip
                        contentStyle={{
                          background: "#0d1120",
                          border: "1px solid #1e2d4a",
                          borderRadius: 4,
                          fontSize: 10,
                          fontFamily: "monospace",
                        }}
                        labelStyle={{ color: "#4b5563" }}
                        formatter={(val: number, name: string) => [
                          `$${fmt(val)}`,
                          name === "ema20" ? "EMA 20" : name === "ema50" ? "EMA 50" : name,
                        ]}
                      />
                      {/* Candle bodies via custom shape */}
                      <Bar
                        dataKey="close"
                        shape={(props: CandleProps) => <CandleShape {...props} />}
                        isAnimationActive={false}
                        maxBarSize={10}
                      >
                        {chartData.map((c, i) => (
                          <Cell key={i} fill={c.close >= c.open ? "#10b981" : "#f87171"} />
                        ))}
                      </Bar>
                      <Line type="monotone" dataKey="ema20" stroke="#22d3ee" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                      <Line type="monotone" dataKey="ema50" stroke="#f59e0b" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                      {price > 0 && (
                        <ReferenceLine
                          y={price}
                          stroke={priceColor}
                          strokeDasharray="4 4"
                          strokeWidth={1}
                          label={{ value: `$${fmt(price)}`, position: "right", fontSize: 9, fill: priceColor }}
                        />
                      )}
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState label="Fetching Binance candle data…" />
                )}
              </div>

              {/* RSI sub-chart */}
              {chartData.length > 0 && (
                <div className="flex-shrink-0" style={{ height: 60 }}>
                  <div className="text-xs pl-1 mb-0.5" style={{ color: "#374151", fontSize: 9 }}>RSI 14</div>
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{ top: 0, right: 2, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="rsiGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%"  stopColor="#8b5cf6" stopOpacity={0.4} />
                          <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <YAxis domain={[0, 100]} hide />
                      <XAxis dataKey="time" hide />
                      <ReferenceLine y={70} stroke="#f87171" strokeDasharray="2 4" strokeWidth={0.8} />
                      <ReferenceLine y={30} stroke="#10b981" strokeDasharray="2 4" strokeWidth={0.8} />
                      <Area type="monotone" dataKey="rsi" stroke="#8b5cf6" strokeWidth={1.2} fill="url(#rsiGrad)" dot={false} isAnimationActive={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>

          {/* Bottom: Positions / History / Metrics */}
          <div className="border-t flex-shrink-0" style={{ height: 200, borderColor: "#111827" }}>
            <div className="flex border-b" style={{ borderColor: "#111827" }}>
              {(["positions", "history", "metrics"] as const).map((tab) => (
                <TabButton key={tab} label={
                  tab === "positions" ? `OPEN POSITIONS (${positions.length})`
                  : tab === "history" ? "TRADE HISTORY"
                  : "SYSTEM METRICS"
                } active={bottomTab === tab} onClick={() => setBottomTab(tab)} />
              ))}
            </div>
            <div className="overflow-y-auto" style={{ height: "calc(100% - 32px)" }}>
              {bottomTab === "positions" && (
                positions.length === 0
                  ? <EmptyState label={isRunning ? "No open positions — monitoring market…" : "Launch system to begin trading"} />
                  : <TradeTable rows={positions} type="open" />
              )}
              {bottomTab === "history" && (
                closedTrades.length === 0
                  ? <EmptyState label="Trade history will appear after first closed trade" />
                  : <TradeTable rows={closedTrades} type="closed" />
              )}
              {bottomTab === "metrics" && <MetricsGrid status={status} closedTrades={closedTrades} />}
            </div>
          </div>
        </div>

        {/* ── RIGHT PANEL: Activity + Orderbook + Equity ─────────────────────── */}
        <div
          className="flex flex-col border-l flex-shrink-0"
          style={{ width: 340, borderColor: "#111827", background: "#090c14" }}
        >
          {/* Tabs */}
          <div className="flex border-b flex-shrink-0" style={{ borderColor: "#111827" }}>
            {(["activity", "book", "equity"] as const).map((tab) => (
              <TabButton
                key={tab}
                label={tab === "activity" ? "AI LOG" : tab === "book" ? "ORDER BOOK" : "EQUITY"}
                active={activeTab === tab}
                onClick={() => setActiveTab(tab)}
                small
              />
            ))}
            {isRunning && activeTab === "activity" && (
              <div className="ml-auto flex items-center gap-1.5 pr-3">
                <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "#10b981" }} />
                <span className="text-xs" style={{ color: "#374151", fontSize: 9 }}>{activity.length}</span>
              </div>
            )}
          </div>

          {/* Activity Log */}
          {activeTab === "activity" && (
            <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5" ref={activityRef}>
              {activity.length === 0 ? (
                <EmptyState label={"Launch the trading system to\nsee real-time AI agent activity"} icon="🤖" />
              ) : (
                activity.map((e) => <ActivityRow key={e.id} entry={e} />)
              )}
            </div>
          )}

          {/* Orderbook */}
          {activeTab === "book" && (
            <div className="flex-1 overflow-hidden flex flex-col">
              <div className="grid grid-cols-2 gap-0 flex-1 overflow-hidden">
                {/* Bids */}
                <div className="overflow-y-auto border-r" style={{ borderColor: "#111827" }}>
                  <div className="sticky top-0 flex justify-between px-2 py-1 text-xs" style={{ background: "#090c14", color: "#374151", fontSize: 9 }}>
                    <span>PRICE</span><span>QTY</span>
                  </div>
                  {(orderBook?.bids ?? []).map((b, i) => (
                    <div key={i} className="relative flex justify-between px-2 py-0.5" style={{ fontSize: 10 }}>
                      <div
                        className="absolute inset-0 opacity-20"
                        style={{ background: "#10b981", width: `${(b.qty / maxBid) * 100}%` }}
                      />
                      <span className="relative tabular-nums" style={{ color: "#10b981" }}>{fmt(b.price)}</span>
                      <span className="relative tabular-nums" style={{ color: "#64748b" }}>{b.qty.toFixed(3)}</span>
                    </div>
                  ))}
                </div>
                {/* Asks */}
                <div className="overflow-y-auto">
                  <div className="sticky top-0 flex justify-between px-2 py-1 text-xs" style={{ background: "#090c14", color: "#374151", fontSize: 9 }}>
                    <span>PRICE</span><span>QTY</span>
                  </div>
                  {(orderBook?.asks ?? []).map((a, i) => (
                    <div key={i} className="relative flex justify-between px-2 py-0.5" style={{ fontSize: 10 }}>
                      <div
                        className="absolute right-0 inset-y-0 opacity-20"
                        style={{ background: "#f87171", width: `${(a.qty / maxAsk) * 100}%` }}
                      />
                      <span className="relative tabular-nums" style={{ color: "#f87171" }}>{fmt(a.price)}</span>
                      <span className="relative tabular-nums" style={{ color: "#64748b" }}>{a.qty.toFixed(3)}</span>
                    </div>
                  ))}
                </div>
              </div>
              {/* Spread */}
              {orderBook && orderBook.bids.length > 0 && orderBook.asks.length > 0 && (
                <div
                  className="flex justify-center items-center gap-4 border-t text-xs py-2"
                  style={{ borderColor: "#111827", color: "#475569" }}
                >
                  <span>Spread: <span className="tabular-nums" style={{ color: "#94a3b8" }}>
                    ${fmt(orderBook.asks[0].price - orderBook.bids[0].price, 4)}
                  </span></span>
                  <span>Mid: <span className="tabular-nums" style={{ color: "#94a3b8" }}>
                    ${fmt((orderBook.asks[0].price + orderBook.bids[0].price) / 2)}
                  </span></span>
                </div>
              )}
            </div>
          )}

          {/* Equity Curve */}
          {activeTab === "equity" && (
            <div className="flex-1 flex flex-col p-2">
              <div className="text-xs mb-1" style={{ color: "#374151", fontSize: 9 }}>
                EQUITY CURVE · Portfolio Balance Over Time
              </div>
              <div className="flex-1">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityHistory} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="2 4" stroke="#111827" vertical={false} />
                    <XAxis dataKey="t" hide />
                    <YAxis tick={{ fontSize: 9, fill: "#374151" }} tickLine={false} axisLine={false} width={60}
                      tickFormatter={(v: number) => `$${fmtK(v)}`} domain={["auto", "auto"]}
                    />
                    <Tooltip
                      contentStyle={{ background: "#0d1120", border: "1px solid #1e2d4a", fontSize: 10, fontFamily: "monospace" }}
                      formatter={(v: number) => [`$${fmt(v)}`, "Equity"]}
                    />
                    <ReferenceLine y={10000} stroke="#374151" strokeDasharray="4 4" strokeWidth={1} />
                    <Area type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={2} fill="url(#eqGrad)" dot={false} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-2">
                {[
                  ["Start Balance", "$10,000.00", "#94a3b8"],
                  ["Win Rate",      "—",          "#94a3b8"],
                  ["Total Trades",  "—",          "#94a3b8"],
                  ["Avg R:R",       "2.0 ×",      "#94a3b8"],
                ].map(([k, v, c]) => (
                  <div key={k} className="rounded p-2" style={{ background: "#0d1120" }}>
                    <div className="text-xs mb-0.5" style={{ color: "#374151", fontSize: 9 }}>{k}</div>
                    <div className="text-sm font-bold tabular-nums" style={{ color: c }}>{v}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ══ status bar ════════════════════════════════════════════════════════ */}
      <div
        className="flex items-center gap-4 px-4 border-t flex-shrink-0"
        style={{ height: 24, borderColor: "#111827", background: "#070a10", fontSize: 9, color: "#374151" }}
      >
        <span>NeuralTrader AI Engine</span>
        <span>·</span>
        <span>Binance Futures Testnet</span>
        <span>·</span>
        <span>Strategy: 7-Stage Breakout + Regime Filter</span>
        <span>·</span>
        {isRunning && status?.startedAt && (
          <span style={{ color: "#10b981" }}>
            Running {fmtDuration(status.startedAt)} · PID {status.pid}
          </span>
        )}
        {!isRunning && <span>IDLE</span>}
        <span className="ml-auto">
          {status?.hasApiKeys === false
            ? "⚠ Set BINANCE_API_KEY + BINANCE_API_SECRET to enable live trading"
            : "✓ Exchange credentials loaded"}
        </span>
      </div>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SectionHeader({ label }: { label: string }) {
  return (
    <div
      className="px-3 py-1.5 text-xs font-bold tracking-widest flex-shrink-0"
      style={{ color: "#1d4ed8", letterSpacing: "0.15em", borderBottom: "1px solid #111827", fontSize: 9 }}
    >
      {label}
    </div>
  );
}

function TabButton({ label, active, onClick, small }: { label: string; active: boolean; onClick: () => void; small?: boolean }) {
  return (
    <button
      onClick={onClick}
      className="px-3 py-2 font-bold tracking-widest transition-all"
      style={{
        fontSize: small ? 9 : 9,
        color: active ? "#60a5fa" : "#374151",
        borderBottom: active ? "2px solid #3b82f6" : "2px solid transparent",
        background: "transparent",
        letterSpacing: "0.1em",
        fontFamily: "inherit",
      }}
    >
      {label}
    </button>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1 text-xs" style={{ color: "#475569", fontSize: 10 }}>
      <span className="inline-block rounded-full w-2 h-2" style={{ background: color }} />
      {label}
    </span>
  );
}

function EmptyState({ label, icon = "—" }: { label: string; icon?: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-xs" style={{ color: "#1f2937" }}>
      <div className="text-3xl mb-2 opacity-30">{icon}</div>
      {label.split("\n").map((l, i) => <div key={i}>{l}</div>)}
    </div>
  );
}

const STATUS_DOT: Record<string, { color: string; glow: boolean }> = {
  idle:   { color: "#1f2937", glow: false },
  active: { color: "#3b82f6", glow: true },
  done:   { color: "#10b981", glow: false },
  skip:   { color: "#374151", glow: false },
  warn:   { color: "#f59e0b", glow: false },
  error:  { color: "#ef4444", glow: true },
};

function AgentPod({ agent, isRunning }: { agent: AgentStage; isRunning: boolean }) {
  const dot = STATUS_DOT[agent.status] ?? STATUS_DOT.idle;
  return (
    <div
      className="rounded p-2 transition-all"
      style={{
        background: agent.status === "active" ? "#0a1628" : "#0c0f18",
        border: `1px solid ${agent.status === "active" ? "#1e3a6e" : "#111827"}`,
      }}
    >
      <div className="flex items-center gap-1.5 mb-0.5">
        <div
          className={`w-2 h-2 rounded-full flex-shrink-0 ${agent.status === "active" ? "animate-pulse" : ""}`}
          style={{ background: dot.color, boxShadow: dot.glow ? `0 0 6px ${dot.color}` : "none" }}
        />
        <span className="font-bold" style={{ fontSize: 10, color: agent.status === "active" ? "#93c5fd" : "#475569" }}>
          {agent.icon} S{agent.stage} {agent.name}
        </span>
      </div>
      <div
        className="pl-3.5 truncate"
        style={{ fontSize: 9, color: "#2d3748" }}
        title={agent.detail}
      >
        {isRunning ? agent.detail : "Standby"}
      </div>
      {agent.calls > 0 && (
        <div className="pl-3.5 mt-0.5" style={{ fontSize: 9, color: "#1f2937" }}>
          Invocations: {agent.calls}
        </div>
      )}
    </div>
  );
}

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  const color = LEVEL_COLOR[entry.level] ?? "#475569";
  const highlight = ["signal", "order", "profit", "loss", "system"].includes(entry.level);
  return (
    <div
      className="flex gap-1.5 leading-tight rounded px-1 py-0.5"
      style={{
        fontSize: 10,
        background:   highlight ? `${color}08` : "transparent",
        borderLeft:   `2px solid ${highlight ? color : "transparent"}`,
        paddingLeft:  highlight ? 5 : 5,
      }}
    >
      <span className="flex-shrink-0 tabular-nums" style={{ color: "#1f2937", fontSize: 9, minWidth: 52 }}>
        {fmtTime(entry.ts)}
      </span>
      <span className="flex-shrink-0 font-bold" style={{ color: "#1e3a6e", fontSize: 9, minWidth: 74 }}>
        [{entry.agent.slice(0, 10)}]
      </span>
      <span style={{ color, wordBreak: "break-word" }}>{entry.msg}</span>
    </div>
  );
}

function TradeTable({ rows, type }: { rows: Record<string, string>[]; type: "open" | "closed" }) {
  if (!rows.length) return null;
  const cols = type === "open"
    ? ["symbol", "direction", "entry_price", "quantity", "stop_loss", "take_profit", "opened_at"]
    : ["symbol", "direction", "entry_price", "close_price", "pnl_usdt", "status", "closed_at"];
  const labels: Record<string, string> = {
    symbol: "SYMBOL", direction: "DIR", entry_price: "ENTRY",
    close_price: "EXIT", pnl_usdt: "P&L (USDT)", status: "STATUS",
    closed_at: "CLOSED", opened_at: "OPENED", quantity: "QTY",
    stop_loss: "SL", take_profit: "TP",
  };
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse" style={{ fontFamily: "monospace" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid #111827" }}>
            {cols.map((c) => (
              <th key={c} className="text-left px-3 py-1.5 font-normal" style={{ color: "#374151", fontSize: 9, whiteSpace: "nowrap" }}>
                {labels[c] ?? c.toUpperCase()}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const pnl = parseFloat(row.pnl_usdt ?? "0");
            return (
              <tr key={i} style={{ borderBottom: "1px solid #0d1120" }}>
                {cols.map((c) => {
                  let color = "#64748b";
                  let val = row[c] ?? "—";
                  if (c === "direction") color = val === "long" ? "#10b981" : "#f87171";
                  if (c === "pnl_usdt") { color = pnl >= 0 ? "#10b981" : "#f87171"; val = `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`; }
                  if (c === "status") color = val.includes("tp") ? "#10b981" : val.includes("sl") ? "#f87171" : "#64748b";
                  if (c === "opened_at" || c === "closed_at") val = val ? new Date(val).toLocaleString("en-US", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
                  return (
                    <td key={c} className="px-3 py-1.5 tabular-nums" style={{ color, whiteSpace: "nowrap", fontSize: 10 }}>
                      {c === "direction" ? val.toUpperCase() : val}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MetricsGrid({ status, closedTrades }: { status: BotStatus | null; closedTrades: Record<string, string>[] }) {
  const wins = closedTrades.filter((t) => parseFloat(t.pnl_usdt ?? "0") > 0).length;
  const totalTrades = closedTrades.length;
  const winRate = totalTrades > 0 ? ((wins / totalTrades) * 100).toFixed(1) : "—";
  const totalPnl = closedTrades.reduce((s, t) => s + parseFloat(t.pnl_usdt ?? "0"), 0);

  const cells = [
    ["Bot Status",     status?.running ? "RUNNING" : "IDLE",          status?.running ? "#10b981" : "#374151"],
    ["Active Pair",    status?.pair ?? "—",                            "#93c5fd"],
    ["Bot PID",        status?.pid ? `${status.pid}` : "—",           "#64748b"],
    ["Cycles Run",     `${status?.cycle ?? 0}`,                       "#94a3b8"],
    ["API Keys",       status?.hasApiKeys ? "LOADED ✓" : "MISSING ✗", status?.hasApiKeys ? "#10b981" : "#f87171"],
    ["Total Trades",   `${totalTrades}`,                               "#94a3b8"],
    ["Win Rate",       winRate !== "—" ? `${winRate}%` : "—",         "#94a3b8"],
    ["Total P&L",      totalTrades > 0 ? `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)} USDT` : "—", totalPnl >= 0 ? "#10b981" : "#f87171"],
    ["Mode",           "Binance Futures Testnet",                      "#64748b"],
  ];
  return (
    <div className="grid grid-cols-3 gap-px p-2" style={{ background: "#070a10" }}>
      {cells.map(([k, v, c]) => (
        <div key={k} className="p-2 rounded" style={{ background: "#0c0f18" }}>
          <div className="mb-0.5" style={{ color: "#1f2937", fontSize: 9 }}>{k}</div>
          <div className="font-bold tabular-nums truncate" style={{ color: c, fontSize: 10 }}>{v}</div>
        </div>
      ))}
    </div>
  );
}
