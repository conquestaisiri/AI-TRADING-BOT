import { useEffect, useRef, useState, useCallback } from "react";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, "").replace("/__mockup", "") + "/api";

// ─── Types ──────────────────────────────────────────────────────────────────
interface AgentStatus {
  id: string;
  name: string;
  stage: number;
  status: "idle" | "active" | "done" | "skip" | "warn";
  lastAction: string;
  calls: number;
}

interface BotStatus {
  running: boolean;
  pair: string | null;
  startedAt: string | null;
  cycle: number;
  balance: number;
  equity: number;
  totalPnl: number;
  winRate: number;
  openTrades: number;
  closedTrades: number;
  lastPrice: number;
  agents: AgentStatus[];
}

interface ActivityEntry {
  id: string;
  type: string;
  level?: string;
  agent?: string;
  msg?: string;
  ts: string;
  raw?: unknown;
}

interface Trade {
  id: string;
  symbol: string;
  side: "LONG" | "SHORT";
  entry: number;
  size: number;
  sl: number;
  tp: number;
  pnl: number;
  status: "OPEN" | "CLOSED";
  openedAt: string;
  closedAt?: string;
  closeReason?: string;
}

interface PriceCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema20: number;
  ema50: number;
  rsi: number;
}

interface EquityPoint {
  time: string;
  equity: number;
  pnl: number;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
const AGENT_ICONS: Record<string, string> = {
  "market-analyst": "🔍",
  "data-fetcher": "📡",
  "indicator-engine": "📊",
  "regime-classifier": "🧭",
  "signal-generator": "⚡",
  "risk-manager": "⚖️",
  "order-executor": "🎯",
};

const LEVEL_COLORS: Record<string, string> = {
  system: "#a78bfa",
  info: "#94a3b8",
  warn: "#f59e0b",
  signal: "#22d3ee",
  order: "#34d399",
  profit: "#10b981",
  loss: "#f87171",
};

const STATUS_COLORS: Record<string, string> = {
  idle: "#4b5563",
  active: "#3b82f6",
  done: "#10b981",
  skip: "#6b7280",
  warn: "#f59e0b",
};

const STATUS_PULSE: Record<string, boolean> = {
  active: true,
};

function fmt(n: number, digits = 2) {
  return n?.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtTime(ts: string) {
  return new Date(ts).toLocaleTimeString("en-US", { hour12: false });
}

function fmtShortTime(ts: string) {
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

// ─── Candlestick custom bar ───────────────────────────────────────────────────
interface CandleBarProps {
  x?: number; y?: number; width?: number; height?: number;
  payload?: PriceCandle & { barMin?: number; barMax?: number };
}

function CandleBar(props: CandleBarProps) {
  const { x = 0, y = 0, width = 0, payload } = props;
  if (!payload) return null;
  const { open, close, high, low } = payload;
  const bull = close >= open;
  const color = bull ? "#10b981" : "#f87171";
  const range = (payload as unknown as { barMax: number; barMin: number });
  const scale = range.barMax - range.barMin;
  if (!scale) return null;

  const toY = (val: number) => y + ((range.barMax - val) / scale) * (props.height ?? 100) + (props.height ?? 100) * 0 ;
  const bodyTop = toY(Math.max(open, close));
  const bodyBot = toY(Math.min(open, close));
  const bodyH = Math.max(1, bodyBot - bodyTop);
  const cx = x + width / 2;
  const wickTop = toY(high);
  const wickBot = toY(low);

  return (
    <g>
      <line x1={cx} y1={wickTop} x2={cx} y2={wickBot} stroke={color} strokeWidth={1} />
      <rect x={x + 1} y={bodyTop} width={Math.max(1, width - 2)} height={bodyH} fill={color} opacity={0.9} />
    </g>
  );
}

// ─── Main Terminal ───────────────────────────────────────────────────────────
export default function TradingTerminal() {
  const [pairs, setPairs] = useState<string[]>([]);
  const [selectedPair, setSelectedPair] = useState("BTCUSDT");
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [openTrades, setOpenTrades] = useState<Trade[]>([]);
  const [closedTrades, setClosedTrades] = useState<Trade[]>([]);
  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([]);
  const [priceHistory, setPriceHistory] = useState<PriceCandle[]>([]);
  const [currentPrice, setCurrentPrice] = useState<number>(0);
  const [priceChange, setPriceChange] = useState<number>(0);
  const [activeTab, setActiveTab] = useState<"open" | "closed" | "metrics">("open");
  const [regime, setRegime] = useState<{ regime: string; score: number } | null>(null);
  const [serverTime, setServerTime] = useState(new Date());
  const [isStarting, setIsStarting] = useState(false);
  const activityEndRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const t = setInterval(() => setServerTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/bot/pairs`)
      .then((r) => r.json())
      .then((p: string[]) => setPairs(p))
      .catch(() => {});
  }, []);

  const pollStatus = useCallback(() => {
    fetch(`${API_BASE}/bot/status`)
      .then((r) => r.json())
      .then((s: BotStatus) => setStatus(s))
      .catch(() => {});
  }, []);

  const pollTrades = useCallback(() => {
    fetch(`${API_BASE}/bot/trades`)
      .then((r) => r.json())
      .then((d: { open: Trade[]; closed: Trade[]; equityCurve: EquityPoint[] }) => {
        setOpenTrades(d.open ?? []);
        setClosedTrades(d.closed ?? []);
        setEquityCurve(d.equityCurve ?? []);
      })
      .catch(() => {});
  }, []);

  const pollMarket = useCallback((pair: string) => {
    fetch(`${API_BASE}/bot/market/${pair}`)
      .then((r) => r.json())
      .then((d: { price: number; change24h: number; priceHistory: PriceCandle[] }) => {
        setCurrentPrice(d.price);
        setPriceChange(d.change24h);
        if (d.priceHistory?.length) setPriceHistory(d.priceHistory.slice(-80));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    pollStatus();
    pollTrades();
    const pair = selectedPair;
    pollMarket(pair);
    const t1 = setInterval(pollStatus, 3000);
    const t2 = setInterval(pollTrades, 5000);
    const t3 = setInterval(() => pollMarket(pair), 4000);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); };
  }, [selectedPair, pollStatus, pollTrades, pollMarket]);

  useEffect(() => {
    if (sseRef.current) { sseRef.current.close(); }
    const es = new EventSource(`${API_BASE}/activity/stream`);
    sseRef.current = es;
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        const entry: ActivityEntry = {
          id: `${Date.now()}-${Math.random()}`,
          type: data.type,
          level: data.level,
          agent: data.agent,
          msg: data.msg,
          ts: data.ts ?? new Date().toISOString(),
          raw: data,
        };
        if (data.type === "price_update") {
          setCurrentPrice(data.price);
          if (data.candle) setPriceHistory((prev) => {
            const next = [...prev, data.candle];
            return next.slice(-80);
          });
        }
        if (data.type === "regime") setRegime({ regime: data.regime, score: data.score });
        if (data.type === "trade_open" || data.type === "trade_close") {
          pollTrades();
          pollStatus();
        }
        if (data.msg) {
          setActivity((prev) => {
            const next = [...prev, entry];
            return next.slice(-200);
          });
        }
      } catch {}
    };
    return () => es.close();
  }, [pollTrades, pollStatus]);

  useEffect(() => {
    activityEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activity]);

  async function handleStart() {
    if (!selectedPair) return;
    setIsStarting(true);
    try {
      await fetch(`${API_BASE}/bot/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair: selectedPair }),
      });
      pollStatus();
    } finally {
      setIsStarting(false);
    }
  }

  async function handleStop() {
    await fetch(`${API_BASE}/bot/stop`, { method: "POST" });
    pollStatus();
  }

  const isRunning = status?.running ?? false;
  const totalPnl = status?.totalPnl ?? 0;
  const pnlColor = totalPnl >= 0 ? "#10b981" : "#f87171";
  const chartData = priceHistory.map((c) => ({
    ...c,
    time: fmtShortTime(c.time),
    barMin: 0,
    barMax: 0,
  }));

  const priceMin = priceHistory.length ? Math.min(...priceHistory.map((c) => c.low)) * 0.9995 : 0;
  const priceMax = priceHistory.length ? Math.max(...priceHistory.map((c) => c.high)) * 1.0005 : 0;

  return (
    <div
      className="flex flex-col w-full h-screen overflow-hidden font-mono"
      style={{ background: "#080b12", color: "#c9d1e0" }}
    >
      {/* ── TOP HEADER ─────────────────────────────────────────────────────── */}
      <header
        className="flex items-center gap-4 px-4 py-2 border-b flex-shrink-0"
        style={{ borderColor: "#1a2035", background: "#0b0f1a" }}
      >
        <div className="flex items-center gap-2 mr-2">
          <div className="w-7 h-7 rounded flex items-center justify-center text-base" style={{ background: "#1d4ed8" }}>⚡</div>
          <span className="text-sm font-bold tracking-widest" style={{ color: "#60a5fa", letterSpacing: "0.15em" }}>NEURALTRADER</span>
          <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: "#1e2d4a", color: "#60a5fa" }}>AI v2.1</span>
        </div>

        {/* Pair ticker strip */}
        <div className="flex gap-4 flex-1">
          {(pairs.length ? pairs : ["BTCUSDT", "ETHUSDT", "SOLUSDT"]).slice(0, 6).map((p) => (
            <button
              key={p}
              onClick={() => !isRunning && setSelectedPair(p)}
              className="flex flex-col items-start text-left px-2 py-0.5 rounded transition-all"
              style={{
                background: selectedPair === p ? "#1a2d50" : "transparent",
                borderBottom: selectedPair === p ? "1px solid #3b82f6" : "1px solid transparent",
              }}
            >
              <span className="text-xs font-bold" style={{ color: selectedPair === p ? "#93c5fd" : "#6b7280" }}>{p.replace("USDT", "")}/USDT</span>
            </button>
          ))}
        </div>

        {/* Status pills */}
        <div className="flex items-center gap-3 text-xs">
          {regime && (
            <span className="px-2 py-0.5 rounded border text-xs" style={{
              borderColor: regime.regime === "TRENDING" ? "#10b981" : regime.regime === "RANGING" ? "#f59e0b" : "#6b7280",
              color: regime.regime === "TRENDING" ? "#10b981" : regime.regime === "RANGING" ? "#f59e0b" : "#6b7280",
              background: "transparent",
            }}>
              {regime.regime} {regime.score}
            </span>
          )}
          <span style={{ color: "#475569" }}>{serverTime.toLocaleTimeString("en-US", { hour12: false })} UTC</span>
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full" style={{ background: isRunning ? "#10b981" : "#374151", boxShadow: isRunning ? "0 0 6px #10b981" : "none" }} />
            <span style={{ color: isRunning ? "#10b981" : "#6b7280" }}>{isRunning ? `LIVE · Cycle ${status?.cycle ?? 0}` : "STANDBY"}</span>
          </div>
        </div>
      </header>

      {/* ── PRICE BAR ──────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-6 px-4 py-2 border-b flex-shrink-0" style={{ borderColor: "#1a2035", background: "#0d1120" }}>
        <div>
          <span className="text-2xl font-bold tabular-nums" style={{ color: priceChange >= 0 ? "#10b981" : "#f87171" }}>
            {currentPrice ? `$${fmt(currentPrice)}` : "—"}
          </span>
          {priceChange !== 0 && (
            <span className="ml-2 text-sm" style={{ color: priceChange >= 0 ? "#10b981" : "#f87171" }}>
              {priceChange >= 0 ? "+" : ""}{priceChange.toFixed(2)}%
            </span>
          )}
        </div>
        <div className="flex gap-6 text-xs" style={{ color: "#64748b" }}>
          <span>Balance <span className="tabular-nums" style={{ color: "#94a3b8" }}>${fmt(status?.balance ?? 10000)}</span></span>
          <span>Equity <span className="tabular-nums" style={{ color: "#94a3b8" }}>${fmt(status?.equity ?? 10000)}</span></span>
          <span>P&L <span className="tabular-nums" style={{ color: pnlColor }}>{totalPnl >= 0 ? "+" : ""}${fmt(totalPnl)}</span></span>
          <span>Win Rate <span className="tabular-nums" style={{ color: "#94a3b8" }}>{fmt(status?.winRate ?? 0, 1)}%</span></span>
          <span>Open <span className="tabular-nums" style={{ color: "#94a3b8" }}>{status?.openTrades ?? 0}</span></span>
          <span>Closed <span className="tabular-nums" style={{ color: "#94a3b8" }}>{status?.closedTrades ?? 0}</span></span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {/* Pair select */}
          <select
            disabled={isRunning}
            value={selectedPair}
            onChange={(e) => setSelectedPair(e.target.value)}
            className="text-xs px-2 py-1 rounded border outline-none"
            style={{ background: "#111827", borderColor: "#1e2d4a", color: "#94a3b8" }}
          >
            {(pairs.length ? pairs : ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]).map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>

          {!isRunning ? (
            <button
              onClick={handleStart}
              disabled={isStarting}
              className="px-4 py-1.5 rounded text-xs font-bold tracking-widest transition-all"
              style={{
                background: isStarting ? "#1e2d4a" : "#1d4ed8",
                color: "#fff",
                boxShadow: isStarting ? "none" : "0 0 12px rgba(29,78,216,0.5)",
                letterSpacing: "0.1em",
              }}
            >
              {isStarting ? "INITIALIZING..." : "▶ LAUNCH SYSTEM"}
            </button>
          ) : (
            <button
              onClick={handleStop}
              className="px-4 py-1.5 rounded text-xs font-bold tracking-widest"
              style={{ background: "#7f1d1d", color: "#fca5a5", letterSpacing: "0.1em" }}
            >
              ⏹ STOP
            </button>
          )}
        </div>
      </div>

      {/* ── MAIN GRID ──────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden gap-0">

        {/* LEFT: Agent Pod Status */}
        <div
          className="flex flex-col gap-1 p-2 border-r overflow-y-auto flex-shrink-0"
          style={{ width: 210, borderColor: "#1a2035", background: "#0b0f1a" }}
        >
          <div className="text-xs font-bold tracking-widest mb-1 px-1" style={{ color: "#3b82f6", letterSpacing: "0.15em" }}>AI AGENTS</div>
          {(status?.agents ?? defaultAgents).map((agent) => (
            <AgentCard key={agent.id} agent={agent} />
          ))}

          <div className="mt-2 border-t pt-2" style={{ borderColor: "#1a2035" }}>
            <div className="text-xs font-bold tracking-widest mb-1 px-1" style={{ color: "#3b82f6", letterSpacing: "0.15em" }}>RISK PARAMS</div>
            <div className="space-y-1">
              {[
                ["Risk/Trade", "1.00%"],
                ["R:R Ratio", "2.0x"],
                ["ATR Mult", "1.5x"],
                ["Max Trades", "3 / 60m"],
                ["Vol Thresh", "1.5x avg"],
                ["Min Regime", "0.50"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs px-1" style={{ color: "#475569" }}>
                  <span>{k}</span>
                  <span style={{ color: "#94a3b8" }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* CENTER: Chart + Positions */}
        <div className="flex flex-col flex-1 overflow-hidden">

          {/* Price Chart */}
          <div className="flex-1 overflow-hidden p-2" style={{ minHeight: 0 }}>
            <div className="flex items-center gap-3 mb-1 px-1">
              <span className="text-xs font-bold tracking-widest" style={{ color: "#3b82f6", letterSpacing: "0.15em" }}>{selectedPair} · 15M</span>
              <div className="flex items-center gap-3 text-xs" style={{ color: "#475569" }}>
                <span><span className="inline-block w-4 h-0.5 mr-1 align-middle" style={{ background: "#22d3ee" }}></span>EMA20</span>
                <span><span className="inline-block w-4 h-0.5 mr-1 align-middle" style={{ background: "#f59e0b" }}></span>EMA50</span>
                <span className="ml-2" style={{ color: priceChange >= 0 ? "#10b981" : "#f87171" }}>
                  {priceHistory.length ? `${priceHistory.length} candles` : "Awaiting data..."}
                </span>
              </div>
            </div>
            <div style={{ height: "calc(100% - 24px)" }}>
              {chartData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1a2035" vertical={false} />
                    <XAxis dataKey="time" tick={{ fontSize: 10, fill: "#374151" }} tickLine={false} axisLine={false} interval={15} />
                    <YAxis
                      domain={[priceMin, priceMax]}
                      tick={{ fontSize: 10, fill: "#374151" }}
                      tickLine={false}
                      axisLine={false}
                      width={68}
                      tickFormatter={(v) => `$${v >= 1000 ? (v / 1000).toFixed(1) + "k" : v.toFixed(2)}`}
                    />
                    <Tooltip
                      contentStyle={{ background: "#0f1729", border: "1px solid #1e2d4a", borderRadius: 4, fontSize: 11 }}
                      labelStyle={{ color: "#64748b" }}
                      itemStyle={{ color: "#94a3b8" }}
                      formatter={(val: number, name: string) => [
                        name === "ema20" || name === "ema50" ? `$${fmt(val)}` : `$${fmt(val)}`,
                        name === "ema20" ? "EMA20" : name === "ema50" ? "EMA50" : name,
                      ]}
                    />
                    <Bar dataKey="close" shape={<CandleBar />} isAnimationActive={false} />
                    <Line type="monotone" dataKey="ema20" stroke="#22d3ee" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="ema50" stroke="#f59e0b" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-full text-xs" style={{ color: "#374151" }}>
                  Select a pair and launch the system to load chart data
                </div>
              )}
            </div>
          </div>

          {/* Bottom tabs: Positions + Metrics */}
          <div className="border-t flex-shrink-0" style={{ borderColor: "#1a2035", height: 220 }}>
            <div className="flex border-b" style={{ borderColor: "#1a2035" }}>
              {(["open", "closed", "metrics"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className="px-4 py-1.5 text-xs font-bold tracking-widest transition-all"
                  style={{
                    color: activeTab === tab ? "#60a5fa" : "#374151",
                    borderBottom: activeTab === tab ? "2px solid #3b82f6" : "2px solid transparent",
                    letterSpacing: "0.1em",
                    background: "transparent",
                  }}
                >
                  {tab === "open" ? `OPEN (${openTrades.length})` : tab === "closed" ? `HISTORY (${closedTrades.length})` : "METRICS"}
                </button>
              ))}
            </div>
            <div className="overflow-y-auto" style={{ height: "calc(100% - 32px)" }}>
              {activeTab === "open" && <TradesTable trades={openTrades} type="open" />}
              {activeTab === "closed" && <TradesTable trades={closedTrades.slice().reverse()} type="closed" />}
              {activeTab === "metrics" && <MetricsPanel equityCurve={equityCurve} status={status} />}
            </div>
          </div>
        </div>

        {/* RIGHT: Activity Log + Equity */}
        <div
          className="flex flex-col border-l flex-shrink-0"
          style={{ width: 360, borderColor: "#1a2035", background: "#0b0f1a" }}
        >
          {/* Activity Log Header */}
          <div className="flex items-center justify-between px-3 py-2 border-b flex-shrink-0" style={{ borderColor: "#1a2035" }}>
            <span className="text-xs font-bold tracking-widest" style={{ color: "#3b82f6", letterSpacing: "0.15em" }}>AI ACTIVITY STREAM</span>
            <div className="flex items-center gap-2">
              {isRunning && <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "#10b981" }} />}
              <span className="text-xs" style={{ color: "#374151" }}>{activity.length} events</span>
            </div>
          </div>

          {/* Scrolling Activity Log */}
          <div className="flex-1 overflow-y-auto p-2 space-y-0.5" style={{ fontFamily: "monospace" }}>
            {activity.length === 0 ? (
              <div className="text-center pt-8 text-xs" style={{ color: "#374151" }}>
                <div className="text-2xl mb-2">🤖</div>
                <div>Launch the system to see</div>
                <div>AI agent activity in real-time</div>
              </div>
            ) : (
              activity.map((entry) => <ActivityRow key={entry.id} entry={entry} />)
            )}
            <div ref={activityEndRef} />
          </div>

          {/* Equity Mini Chart */}
          <div className="border-t flex-shrink-0" style={{ borderColor: "#1a2035", height: 130 }}>
            <div className="flex items-center justify-between px-3 pt-2 mb-1">
              <span className="text-xs font-bold tracking-widest" style={{ color: "#3b82f6", letterSpacing: "0.15em" }}>EQUITY CURVE</span>
              <span className="text-xs tabular-nums" style={{ color: pnlColor }}>
                {totalPnl >= 0 ? "+" : ""}${fmt(totalPnl)}
              </span>
            </div>
            <div style={{ height: 90 }}>
              {equityCurve.length > 1 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityCurve} margin={{ top: 2, right: 8, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="time" hide />
                    <YAxis hide domain={["auto", "auto"]} />
                    <Tooltip
                      contentStyle={{ background: "#0f1729", border: "1px solid #1e2d4a", fontSize: 10 }}
                      formatter={(v: number) => [`$${fmt(v)}`, "Equity"]}
                    />
                    <ReferenceLine y={10000} stroke="#374151" strokeDasharray="3 3" />
                    <Area type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={1.5} fill="url(#eqGrad)" dot={false} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-full text-xs" style={{ color: "#374151" }}>Awaiting trades...</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function AgentCard({ agent }: { agent: AgentStatus }) {
  const icon = AGENT_ICONS[agent.id] ?? "🤖";
  const color = STATUS_COLORS[agent.status] ?? "#4b5563";
  const pulsing = STATUS_PULSE[agent.status] ?? false;

  return (
    <div
      className="rounded p-2 transition-all"
      style={{
        background: agent.status === "active" ? "#0f1e3a" : "#0d1120",
        border: `1px solid ${agent.status === "active" ? "#1e3a6e" : "#111827"}`,
      }}
    >
      <div className="flex items-center gap-2 mb-0.5">
        <div
          className={`w-2 h-2 rounded-full flex-shrink-0 ${pulsing ? "animate-pulse" : ""}`}
          style={{ background: color, boxShadow: pulsing ? `0 0 6px ${color}` : "none" }}
        />
        <span className="text-xs font-bold" style={{ color: agent.status === "active" ? "#93c5fd" : "#94a3b8" }}>
          {icon} {agent.name}
        </span>
        <span className="ml-auto text-xs" style={{ color: "#475569" }}>S{agent.stage}</span>
      </div>
      <div className="text-xs leading-tight pl-4" style={{ color: "#475569" }} title={agent.lastAction}>
        {agent.lastAction.length > 28 ? agent.lastAction.slice(0, 28) + "…" : agent.lastAction}
      </div>
      {agent.calls > 0 && (
        <div className="text-xs pl-4 mt-0.5" style={{ color: "#374151" }}>Calls: {agent.calls}</div>
      )}
    </div>
  );
}

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  const color = LEVEL_COLORS[entry.level ?? "info"] ?? "#64748b";
  const isImportant = ["signal", "order", "profit", "loss", "system"].includes(entry.level ?? "");

  return (
    <div
      className="flex gap-2 py-0.5 px-1 rounded text-xs leading-tight"
      style={{
        background: isImportant ? "rgba(59,130,246,0.04)" : "transparent",
        borderLeft: isImportant ? `2px solid ${color}` : "2px solid transparent",
        paddingLeft: isImportant ? 6 : 6,
      }}
    >
      <span className="flex-shrink-0 tabular-nums" style={{ color: "#374151", fontSize: 10 }}>
        {fmtTime(entry.ts)}
      </span>
      {entry.agent && (
        <span className="flex-shrink-0 font-bold" style={{ color: "#3b82f6", fontSize: 10, minWidth: 90 }}>
          [{entry.agent}]
        </span>
      )}
      <span style={{ color, wordBreak: "break-word" }}>{entry.msg}</span>
    </div>
  );
}

function TradesTable({ trades, type }: { trades: Trade[]; type: "open" | "closed" }) {
  if (trades.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs" style={{ color: "#374151" }}>
        {type === "open" ? "No open positions" : "No trade history"}
      </div>
    );
  }
  return (
    <table className="w-full text-xs border-collapse">
      <thead>
        <tr style={{ color: "#374151", borderBottom: "1px solid #1a2035" }}>
          <th className="text-left px-3 py-1 font-normal">Symbol</th>
          <th className="text-left px-2 py-1 font-normal">Side</th>
          <th className="text-right px-2 py-1 font-normal">Entry</th>
          <th className="text-right px-2 py-1 font-normal">Size</th>
          <th className="text-right px-2 py-1 font-normal">SL / TP</th>
          {type === "closed" && <th className="text-right px-2 py-1 font-normal">P&L</th>}
          {type === "open" && <th className="text-right px-2 py-1 font-normal">Unrealised</th>}
          {type === "closed" && <th className="text-left px-2 py-1 font-normal">Reason</th>}
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr key={t.id} style={{ borderBottom: "1px solid #111827" }}>
            <td className="px-3 py-1.5" style={{ color: "#94a3b8" }}>{t.symbol}</td>
            <td className="px-2 py-1.5">
              <span
                className="px-1.5 py-0.5 rounded text-xs font-bold"
                style={{
                  background: t.side === "LONG" ? "#052e16" : "#450a0a",
                  color: t.side === "LONG" ? "#34d399" : "#f87171",
                }}
              >
                {t.side}
              </span>
            </td>
            <td className="px-2 py-1.5 text-right tabular-nums" style={{ color: "#94a3b8" }}>${fmt(t.entry)}</td>
            <td className="px-2 py-1.5 text-right tabular-nums" style={{ color: "#64748b" }}>{t.size}</td>
            <td className="px-2 py-1.5 text-right text-xs" style={{ color: "#4b5563" }}>
              <span style={{ color: "#f87171" }}>{fmt(t.sl)}</span>
              {" / "}
              <span style={{ color: "#34d399" }}>{fmt(t.tp)}</span>
            </td>
            {type === "closed" && (
              <td className="px-2 py-1.5 text-right tabular-nums font-bold" style={{ color: t.pnl >= 0 ? "#10b981" : "#f87171" }}>
                {t.pnl >= 0 ? "+" : ""}${fmt(t.pnl)}
              </td>
            )}
            {type === "open" && (
              <td className="px-2 py-1.5 text-right tabular-nums" style={{ color: "#64748b" }}>—</td>
            )}
            {type === "closed" && (
              <td className="px-2 py-1.5 text-xs" style={{ color: t.closeReason === "TP HIT" ? "#10b981" : "#f87171" }}>
                {t.closeReason}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MetricsPanel({ equityCurve, status }: { equityCurve: EquityPoint[]; status: BotStatus | null }) {
  const totalPnl = status?.totalPnl ?? 0;
  const pnlPct = ((totalPnl / 10000) * 100).toFixed(2);
  const metrics = [
    ["Starting Balance", "$10,000.00"],
    ["Current Equity", `$${fmt(status?.equity ?? 10000)}`],
    ["Total P&L", `${totalPnl >= 0 ? "+" : ""}$${fmt(totalPnl)}`],
    ["Return", `${totalPnl >= 0 ? "+" : ""}${pnlPct}%`],
    ["Win Rate", `${fmt(status?.winRate ?? 0, 1)}%`],
    ["Total Trades", `${status?.closedTrades ?? 0}`],
    ["Risk/Trade", "1.00%"],
    ["Reward:Risk", "2.0x"],
    ["Active Cycle", `${status?.cycle ?? 0}`],
  ];
  return (
    <div className="grid grid-cols-3 gap-px p-2" style={{ background: "#080b12" }}>
      {metrics.map(([k, v]) => (
        <div key={k} className="p-2 rounded" style={{ background: "#0d1120" }}>
          <div className="text-xs mb-0.5" style={{ color: "#374151" }}>{k}</div>
          <div className="text-sm font-bold tabular-nums" style={{
            color: k.includes("P&L") || k.includes("Return")
              ? totalPnl >= 0 ? "#10b981" : "#f87171"
              : "#94a3b8"
          }}>{v}</div>
        </div>
      ))}
    </div>
  );
}

const defaultAgents: AgentStatus[] = [
  { id: "market-analyst", name: "Market Analyst", stage: 1, status: "idle", lastAction: "Awaiting pair selection", calls: 0 },
  { id: "data-fetcher", name: "Data Fetcher", stage: 2, status: "idle", lastAction: "Standby", calls: 0 },
  { id: "indicator-engine", name: "Indicator Engine", stage: 3, status: "idle", lastAction: "Standby", calls: 0 },
  { id: "regime-classifier", name: "Regime Classifier", stage: 4, status: "idle", lastAction: "Standby", calls: 0 },
  { id: "signal-generator", name: "Signal Generator", stage: 5, status: "idle", lastAction: "Standby", calls: 0 },
  { id: "risk-manager", name: "Risk Manager", stage: 6, status: "idle", lastAction: "Standby", calls: 0 },
  { id: "order-executor", name: "Order Executor", stage: 7, status: "idle", lastAction: "Standby", calls: 0 },
];
