/**
 * NeuralTrader — AI-Powered Crypto Trading Terminal
 * Sophisticated, real-time, fully-wired trading workstation
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Area, AreaChart, ComposedChart, CartesianGrid,
  Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

const API = "/api";

// ─── Color palette ────────────────────────────────────────────────────────────
const C = {
  bg:       "#07080d",
  panel:    "#0b0d17",
  card:     "#0f1420",
  border:   "#1a2035",
  border2:  "#222a45",
  green:    "#00e676",
  greenDim: "#00e67618",
  cyan:     "#29b6f6",
  cyanDim:  "#29b6f618",
  amber:    "#ffa726",
  amberDim: "#ffa72618",
  red:      "#ef5350",
  redDim:   "#ef535018",
  purple:   "#ce93d8",
  purpleDim:"#ce93d818",
  blue:     "#42a5f5",
  text:     "#e2e8f0",
  textDim:  "#4a5568",
  textMid:  "#718096",
};

// ─── Types ─────────────────────────────────────────────────────────────────────
interface BotStatus {
  running: boolean; pair: string | null; symbols?: string[];
  pid: number | null; startedAt: string | null; cycle: number;
  lastLogLine: string; error: string | null; hasApiKeys: boolean;
}
interface Candle {
  time: string; open: number; high: number; low: number; close: number;
  volume: number; ema20: number; ema50: number; rsi: number;
}
interface MarketData {
  pair: string; price: number; change24h: number; high24h: number;
  low24h: number; volume24h: number; priceHistory: Candle[];
}
interface TickerItem { symbol: string; price: number; change: number; }
interface OrderBookEntry { price: number; qty: number; }
interface OrderBook { bids: OrderBookEntry[]; asks: OrderBookEntry[]; }
interface ActivityEntry {
  id: string; type: string; level: string; agent: string; msg: string; ts: string;
}
interface ProviderHealth {
  binance: boolean; openRouter: boolean; groq: boolean; ollama: boolean; aiReady: boolean;
}
interface Settings {
  settings: Record<string, string>;
  sources: Record<string, string>;
  health: ProviderHealth;
}
interface OpenTrade {
  id?: string; symbol?: string; side?: string;
  entry_price?: string | number; stop_loss?: string | number;
  take_profit?: string | number; quantity?: string | number; opened_at?: string;
}
interface ClosedTrade {
  symbol?: string; side?: string; entry_price?: string; exit_price?: string;
  pnl?: string; opened_at?: string; closed_at?: string; reason?: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
const fmt = (n: number | string | undefined, d = 2) => {
  const v = Number(n);
  return isNaN(v) ? "—" : v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
};
const fmtPrice = (n: number) => n >= 10000 ? fmt(n, 0) : n >= 100 ? fmt(n, 2) : n >= 1 ? fmt(n, 4) : fmt(n, 6);
const fmtTime = (iso: string) => {
  try { return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }); }
  catch { return iso?.slice(11, 19) ?? ""; }
};
const fmtDateTime = (iso?: string) => {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString("en-US", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }); }
  catch { return iso; }
};

// ─── Activity level config ─────────────────────────────────────────────────────
const lvl: Record<string, { color: string; bg: string; label: string }> = {
  order:  { color: C.cyan,   bg: C.cyanDim,   label: "ORDER"  },
  signal: { color: C.purple, bg: C.purpleDim,  label: "SIGNAL" },
  profit: { color: C.green,  bg: C.greenDim,   label: "PROFIT" },
  loss:   { color: C.red,    bg: C.redDim,     label: "LOSS"   },
  error:  { color: C.red,    bg: C.redDim,     label: "ERROR"  },
  warn:   { color: C.amber,  bg: C.amberDim,   label: "WARN"   },
  ai:     { color: "#ba68c8",bg: "#ba68c818",  label: "AI"     },
  system: { color: C.blue,   bg: "#42a5f518",  label: "SYS"    },
  info:   { color: C.textMid,bg: "transparent",label: "INFO"   },
};

// ─── Main Terminal ─────────────────────────────────────────────────────────────
export default function TradingTerminal() {
  const [botStatus, setBotStatus] = useState<BotStatus>({
    running: false, pair: "BTCUSDT", symbols: ["BTCUSDT"],
    pid: null, startedAt: null, cycle: 0, lastLogLine: "", error: null, hasApiKeys: false,
  });
  const [marketData, setMarketData] = useState<MarketData | null>(null);
  const [tickers, setTickers] = useState<TickerItem[]>([]);
  const [orderBook, setOrderBook] = useState<OrderBook>({ bids: [], asks: [] });
  const [activities, setActivities] = useState<ActivityEntry[]>([]);
  const [openTrades, setOpenTrades] = useState<OpenTrade[]>([]);
  const [closedTrades, setClosedTrades] = useState<ClosedTrade[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [allPairs, setAllPairs] = useState<string[]>([]);
  const [selectedPairs, setSelectedPairs] = useState<string[]>(["BTCUSDT", "ETHUSDT"]);
  const [chartPair, setChartPair] = useState("BTCUSDT");
  const [showSettings, setShowSettings] = useState(false);
  const [showPairSearch, setShowPairSearch] = useState(false);
  const [pairSearch, setPairSearch] = useState("");
  const [activeTab, setActiveTab] = useState<"chart" | "orderbook">("chart");
  const [activeBottomTab, setActiveBottomTab] = useState<"positions" | "history">("positions");
  const [settingsForm, setSettingsForm] = useState<Record<string, string>>({});
  const [savingSettings, setSavingSettings] = useState(false);
  const [saveSettingsError, setSaveSettingsError] = useState<string | null>(null);
  const [loadingPairs, setLoadingPairs] = useState(false);
  const activityRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);

  // ── Fetchers ──────────────────────────────────────────────────────────────
  const fetchStatus = useCallback(async () => {
    try { const r = await fetch(`${API}/bot/status`); if (r.ok) setBotStatus(await r.json()); } catch {}
  }, []);

  const fetchMarket = useCallback(async (pair: string) => {
    try { const r = await fetch(`${API}/bot/market/${pair}`); if (r.ok) setMarketData({ pair, ...(await r.json()) }); } catch {}
  }, []);

  const fetchTickers = useCallback(async (pairs: string[]) => {
    try {
      const r = await fetch(`${API}/bot/tickers?pairs=${pairs.join(",")}`);
      if (r.ok) setTickers(await r.json());
    } catch {}
  }, []);

  const fetchOrderBook = useCallback(async (pair: string) => {
    try { const r = await fetch(`${API}/bot/orderbook/${pair}`); if (r.ok) setOrderBook(await r.json()); } catch {}
  }, []);

  const fetchTrades = useCallback(async () => {
    try {
      const r = await fetch(`${API}/bot/trades`);
      if (r.ok) { const d = await r.json(); setOpenTrades(d.open ?? []); setClosedTrades(d.closed ?? []); }
    } catch {}
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const r = await fetch(`${API}/settings`);
      if (r.ok) { const d: Settings = await r.json(); setSettings(d); setSettingsForm(d.settings); }
    } catch {}
  }, []);

  const fetchAllPairs = useCallback(async () => {
    setLoadingPairs(true);
    try { const r = await fetch(`${API}/bot/all-pairs`); if (r.ok) setAllPairs(await r.json()); } catch {}
    setLoadingPairs(false);
  }, []);

  // ── SSE Activity Stream ────────────────────────────────────────────────────
  useEffect(() => {
    const connect = () => {
      if (sseRef.current) sseRef.current.close();
      const es = new EventSource(`${API}/activity/stream`);
      sseRef.current = es;
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "connected") return;
          const entry: ActivityEntry = {
            id: `${Date.now()}-${Math.random()}`,
            type: data.type ?? "log", level: data.level ?? "info",
            agent: data.agent ?? "System", msg: data.msg ?? "",
            ts: data.ts ?? new Date().toISOString(),
          };
          setActivities((prev) => [entry, ...prev].slice(0, 500));
        } catch {}
      };
      es.onerror = () => { es.close(); setTimeout(connect, 3000); };
    };
    connect();
    return () => { sseRef.current?.close(); };
  }, []);

  // ── Polling ────────────────────────────────────────────────────────────────
  useEffect(() => {
    const pairs = selectedPairs.length ? selectedPairs : ["BTCUSDT"];
    fetchStatus(); fetchMarket(chartPair); fetchTickers(pairs);
    fetchSettings(); fetchTrades(); fetchOrderBook(chartPair);
    const intervals = [
      setInterval(fetchStatus, 2000),
      setInterval(() => fetchMarket(chartPair), 5000),
      setInterval(() => fetchTickers(pairs), 3000),
      setInterval(() => fetchOrderBook(chartPair), 2000),
      setInterval(fetchTrades, 5000),
      setInterval(fetchSettings, 15000),
    ];
    return () => intervals.forEach(clearInterval);
  }, [chartPair, selectedPairs, fetchStatus, fetchMarket, fetchTickers, fetchOrderBook, fetchTrades, fetchSettings]);

  // ── Bot control ────────────────────────────────────────────────────────────
  const startBot = async () => {
    const symbols = selectedPairs.length ? selectedPairs : ["BTCUSDT"];
    await fetch(`${API}/bot/start`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols }),
    });
    setTimeout(fetchStatus, 500);
  };

  const stopBot = async () => {
    await fetch(`${API}/bot/stop`, { method: "POST" });
    setTimeout(fetchStatus, 500);
  };

  // ── Settings save ──────────────────────────────────────────────────────────
  const saveSettings = async () => {
    setSavingSettings(true);
    setSaveSettingsError(null);
    try {
      const res = await fetch(`${API}/settings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsForm),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "Unknown error");
        setSaveSettingsError(`Save failed (${res.status}): ${text.slice(0, 120)}`);
        return;
      }
      await fetchSettings();
      setShowSettings(false);
    } catch (err) {
      setSaveSettingsError(`Network error: ${String(err)}`);
    } finally {
      setSavingSettings(false);
    }
  };

  // ── Pair management ────────────────────────────────────────────────────────
  const togglePair = (pair: string) => {
    setSelectedPairs((prev) =>
      prev.includes(pair) ? prev.filter((p) => p !== pair) : [...prev, pair].slice(0, 20)
    );
  };

  const knownPairs = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT",
    "LINKUSDT","DOTUSDT","MATICUSDT","LTCUSDT","UNIUSDT","ATOMUSDT","NEARUSDT",
    "FTMUSDT","AAVEUSDT","SHIBUSDT","TRXUSDT","ALGOUSDT","SANDUSDT","MANAUSDT",
    "GALAUSDT","AXSUSDT","INJUSDT","APTUSDT","ARBUSDT","OPUSDT","SUIUSDT","PEPEUSDT",
    "WLDUSDT","TIAUSDT","SEIUSDT","STXUSDT","RENDERUSDT","FETUSDT","AGIXUSDT",
    "IMXUSDT","RUNEUSDT","MINAUSDT","FILUSDT","HBARUSDT","VETUSDT","ICPUSDT",
  ];
  const sourcePairs = allPairs.length ? allPairs : knownPairs;
  const filteredPairs = pairSearch
    ? sourcePairs.filter((p) => p.toLowerCase().includes(pairSearch.toLowerCase()))
    : sourcePairs;

  const currentPrice = marketData?.price ?? 0;
  const currentChange = marketData?.change24h ?? 0;
  const latestCandle = marketData?.priceHistory?.slice(-1)[0];
  const rsi = latestCandle?.rsi ?? 50;

  // ─── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: "'Inter', system-ui, sans-serif", color: C.text, display: "flex", flexDirection: "column", overflow: "hidden" }}>

      {/* TOP BAR */}
      <TopBar
        tickers={tickers} chartPair={chartPair} setChartPair={setChartPair}
        botStatus={botStatus} onSettings={() => setShowSettings(true)}
      />

      {/* BODY */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "260px 1fr 290px", overflow: "hidden", minHeight: 0 }}>

        {/* LEFT SIDEBAR */}
        <div style={{ borderRight: `1px solid ${C.border}`, overflow: "auto", display: "flex", flexDirection: "column" }}>
          <ControlPanel
            botStatus={botStatus} selectedPairs={selectedPairs} chartPair={chartPair}
            setChartPair={setChartPair} onStart={startBot} onStop={stopBot}
            onAddPairs={() => { setShowPairSearch(true); if (!allPairs.length) fetchAllPairs(); }}
            onRemovePair={togglePair}
          />
          <ProviderHealthPanel health={settings?.health} />
          <SignalSummaryPanel marketData={marketData} rsi={rsi} />
          <RiskPanel openTrades={openTrades} closedTrades={closedTrades} settings={settings} />
        </div>

        {/* CENTER */}
        <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* Tab bar */}
          <div style={{ background: C.panel, borderBottom: `1px solid ${C.border}`, padding: "0 12px", height: 38, display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
            <TabBtn active={activeTab === "chart"} onClick={() => setActiveTab("chart")}>📈 CHART</TabBtn>
            <TabBtn active={activeTab === "orderbook"} onClick={() => setActiveTab("orderbook")}>📊 ORDER BOOK</TabBtn>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 10, color: C.textDim, fontFamily: "monospace" }}>
              {chartPair} · 15m · OKX  {currentPrice > 0 && <span style={{ color: currentChange >= 0 ? C.green : C.red }}>  {currentChange >= 0 ? "+" : ""}{fmt(currentChange)}%</span>}
            </span>
          </div>

          {/* Chart/OB area */}
          <div style={{ flex: "0 0 auto", padding: "8px 10px", background: C.bg }}>
            {activeTab === "chart" ? (
              <>
                <PriceChart data={marketData?.priceHistory ?? []} />
                <RSIChart data={marketData?.priceHistory ?? []} />
              </>
            ) : (
              <OrderBookView orderBook={orderBook} />
            )}
          </div>

          {/* AI Council */}
          <div style={{ borderTop: `1px solid ${C.border}`, padding: "8px 10px", background: C.bg, flexShrink: 0 }}>
            <SectionHeader>🤖 AI COUNCIL</SectionHeader>
            <AICouncilPanel running={botStatus.running} aiReady={settings?.health.aiReady ?? false} activities={activities} />
          </div>

          {/* Positions / History */}
          <div style={{ borderTop: `1px solid ${C.border}`, flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
            <div style={{ background: C.panel, borderBottom: `1px solid ${C.border}`, padding: "0 12px", height: 34, display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
              <TabBtn active={activeBottomTab === "positions"} onClick={() => setActiveBottomTab("positions")}>
                POSITIONS ({openTrades.length})
              </TabBtn>
              <TabBtn active={activeBottomTab === "history"} onClick={() => setActiveBottomTab("history")}>
                HISTORY ({closedTrades.length})
              </TabBtn>
            </div>
            <div style={{ overflow: "auto", flex: 1, padding: "4px 10px" }}>
              {activeBottomTab === "positions"
                ? <TradeTable trades={openTrades} type="open" />
                : <TradeTable trades={closedTrades} type="closed" />}
            </div>
          </div>
        </div>

        {/* RIGHT: Activity Stream */}
        <div style={{ borderLeft: `1px solid ${C.border}`, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ background: C.panel, borderBottom: `1px solid ${C.border}`, padding: "10px 12px", flexShrink: 0, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: 1.5, color: C.textDim }}>AGENT ACTIVITY LOG</span>
            <span style={{ fontSize: 9, color: C.textDim }}>{activities.length} events</span>
          </div>
          <div ref={activityRef} style={{ flex: 1, overflow: "auto" }}>
            {activities.length === 0 && (
              <div style={{ padding: "32px 16px", textAlign: "center", color: C.textDim, fontSize: 11 }}>
                <div style={{ fontSize: 28, marginBottom: 12 }}>📡</div>
                <div style={{ marginBottom: 4 }}>Waiting for activity...</div>
                <div style={{ fontSize: 10 }}>Start the bot to see real-time events</div>
              </div>
            )}
            {activities.map((a) => {
              const s = lvl[a.level] ?? lvl.info;
              return (
                <div key={a.id} style={{ padding: "5px 10px", borderBottom: `1px solid ${C.border}18` }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}>
                    <span style={{ fontSize: 8, fontWeight: 700, padding: "1px 4px", borderRadius: 3, background: s.bg, color: s.color, minWidth: 36, textAlign: "center", fontFamily: "monospace" }}>{s.label}</span>
                    <span style={{ fontSize: 9, color: C.textDim, fontFamily: "monospace" }}>{fmtTime(a.ts)}</span>
                    <span style={{ fontSize: 9, color: C.purple, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.agent}</span>
                  </div>
                  <div style={{ fontSize: 10, color: s.color === C.textMid ? C.textMid : s.color, paddingLeft: 41, lineHeight: 1.5, wordBreak: "break-word" }}>{a.msg}</div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* PAIR SEARCH MODAL */}
      {showPairSearch && (
        <Modal title="SELECT TRADING PAIRS" onClose={() => { setShowPairSearch(false); setPairSearch(""); }} width={500}>
          <div style={{ padding: "12px 16px 8px" }}>
            <input value={pairSearch} onChange={(e) => setPairSearch(e.target.value)}
              placeholder="Search 1000+ pairs... e.g. BTC, ETH, SOL, PEPE"
              autoFocus
              style={{ width: "100%", background: C.card, border: `1px solid ${C.border2}`, borderRadius: 8, padding: "8px 12px", color: C.text, fontSize: 12, outline: "none", boxSizing: "border-box" }} />
            <div style={{ display: "flex", justifyContent: "space-between", margin: "8px 0", fontSize: 10, color: C.textDim }}>
              <span>{loadingPairs ? "Loading pairs..." : `${filteredPairs.length} pairs available`}</span>
              <span style={{ color: C.textMid }}>{selectedPairs.length} / 20 selected</span>
            </div>
          </div>
          <div style={{ maxHeight: 380, overflow: "auto", padding: "0 16px 16px", display: "flex", flexWrap: "wrap", gap: 5 }}>
            {filteredPairs.slice(0, 250).map((p) => {
              const sel = selectedPairs.includes(p);
              return (
                <button key={p} onClick={() => togglePair(p)}
                  style={{ padding: "4px 10px", borderRadius: 5, border: `1px solid ${sel ? C.green : C.border}`, background: sel ? C.greenDim : "transparent", color: sel ? C.green : C.textMid, fontSize: 10, fontWeight: sel ? 700 : 400, cursor: "pointer", fontFamily: "monospace", transition: "all 0.15s" }}>
                  {p.replace("USDT", "")}
                </button>
              );
            })}
          </div>
          <div style={{ padding: "10px 16px", borderTop: `1px solid ${C.border}`, display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Btn onClick={() => { setShowPairSearch(false); setPairSearch(""); }}>Cancel</Btn>
            <Btn primary onClick={() => { setShowPairSearch(false); setPairSearch(""); }}>
              Confirm ({selectedPairs.length} pairs)
            </Btn>
          </div>
        </Modal>
      )}

      {/* SETTINGS MODAL */}
      {showSettings && (
        <SettingsModal
          form={settingsForm}
          onChange={(k, v) => setSettingsForm((prev) => ({ ...prev, [k]: v }))}
          onSave={saveSettings}
          onClose={() => { setShowSettings(false); setSaveSettingsError(null); }}
          saving={savingSettings}
          health={settings?.health}
          saveError={saveSettingsError}
        />
      )}

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1}50%{opacity:0.35} }
        @keyframes blink { 0%,100%{opacity:1}50%{opacity:0.6} }
        ::-webkit-scrollbar{width:4px;height:4px}
        ::-webkit-scrollbar-track{background:transparent}
        ::-webkit-scrollbar-thumb{background:#1a2035;border-radius:4px}
        *{box-sizing:border-box}
        button,input,select{font-family:inherit}
      `}</style>
    </div>
  );
}

// ─── TOP BAR ─────────────────────────────────────────────────────────────────
function TopBar({ tickers, chartPair, setChartPair, botStatus, onSettings }: {
  tickers: TickerItem[]; chartPair: string; setChartPair: (p: string) => void;
  botStatus: BotStatus; onSettings: () => void;
}) {
  return (
    <div style={{ background: C.panel, borderBottom: `1px solid ${C.border}`, height: 50, padding: "0 14px", display: "flex", alignItems: "center", gap: 14, flexShrink: 0 }}>
      {/* Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 170 }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, background: `linear-gradient(135deg, ${C.green} 0%, ${C.cyan} 100%)`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 15, fontWeight: 800, flexShrink: 0 }}>⚡</div>
        <div>
          <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: 0.5, color: C.text }}>
            NEURAL<span style={{ color: C.green }}>TRADER</span>
          </div>
          <div style={{ fontSize: 8, color: C.textDim, letterSpacing: 2 }}>AI TRADING TERMINAL</div>
        </div>
      </div>

      {/* Tickers */}
      <div style={{ flex: 1, display: "flex", gap: 2, overflow: "hidden" }}>
        {tickers.slice(0, 10).map((t) => (
          <button key={t.symbol} onClick={() => setChartPair(t.symbol)}
            style={{ flexShrink: 0, background: chartPair === t.symbol ? C.border2 : "transparent", border: `1px solid ${chartPair === t.symbol ? C.green + "40" : "transparent"}`, borderRadius: 7, padding: "3px 9px", cursor: "pointer" }}>
            <div style={{ fontSize: 9, color: C.textDim, fontWeight: 600, letterSpacing: 0.5 }}>{t.symbol.replace("USDT", "")}</div>
            <div style={{ fontSize: 11, fontWeight: 700, color: C.text, fontFamily: "monospace" }}>{fmtPrice(t.price)}</div>
            <div style={{ fontSize: 9, color: t.change >= 0 ? C.green : C.red, fontFamily: "monospace" }}>{t.change >= 0 ? "+" : ""}{fmt(t.change)}%</div>
          </button>
        ))}
      </div>

      {/* Status + Controls */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 12px", borderRadius: 20, background: botStatus.running ? C.greenDim : "#1a1f35", border: `1px solid ${botStatus.running ? C.green + "60" : C.border}` }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: botStatus.running ? C.green : C.textDim, boxShadow: botStatus.running ? `0 0 8px ${C.green}` : "none", animation: botStatus.running ? "pulse 2s infinite" : "none" }} />
          <span style={{ fontSize: 10, fontWeight: 700, color: botStatus.running ? C.green : C.textDim, letterSpacing: 0.5 }}>
            {botStatus.running ? `LIVE · CYCLE ${botStatus.cycle}` : "STANDBY"}
          </span>
        </div>
        {!botStatus.hasApiKeys && (
          <div style={{ fontSize: 9, color: C.amber, background: C.amberDim, padding: "3px 8px", borderRadius: 4, fontWeight: 600 }}>⚠ NO API KEYS</div>
        )}
        <button onClick={onSettings}
          style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "6px 14px", cursor: "pointer", fontSize: 10, color: C.textMid, fontWeight: 600, letterSpacing: 0.5 }}>
          ⚙ SETTINGS
        </button>
      </div>
    </div>
  );
}

// ─── CONTROL PANEL ─────────────────────────────────────────────────────────────
function ControlPanel({ botStatus, selectedPairs, chartPair, setChartPair, onStart, onStop, onAddPairs, onRemovePair }: {
  botStatus: BotStatus; selectedPairs: string[]; chartPair: string;
  setChartPair: (p: string) => void; onStart: () => void; onStop: () => void;
  onAddPairs: () => void; onRemovePair: (p: string) => void;
}) {
  return (
    <PanelBox title="SYSTEM CONTROL" accent={C.green}>
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 9, color: C.textDim, fontWeight: 600, letterSpacing: 1, marginBottom: 5 }}>ACTIVE PAIRS ({selectedPairs.length})</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
          {selectedPairs.map((p) => (
            <div key={p} onClick={() => setChartPair(p)}
              style={{ display: "flex", alignItems: "center", gap: 3, padding: "3px 7px", borderRadius: 5, background: p === chartPair ? C.greenDim : "#1a2035", border: `1px solid ${p === chartPair ? C.green + "60" : C.border}`, cursor: "pointer" }}>
              <span style={{ fontSize: 10, color: p === chartPair ? C.green : C.text, fontWeight: 600, fontFamily: "monospace" }}>{p.replace("USDT", "")}</span>
              <span onClick={(e) => { e.stopPropagation(); onRemovePair(p); }} style={{ fontSize: 10, color: C.textDim, lineHeight: 1, cursor: "pointer" }}>×</span>
            </div>
          ))}
        </div>
        <button onClick={onAddPairs}
          style={{ width: "100%", background: "transparent", border: `1px dashed ${C.border2}`, borderRadius: 6, padding: "6px", color: C.textDim, fontSize: 10, cursor: "pointer", textAlign: "center" }}>
          + Add / search pairs
        </button>
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <button onClick={onStart} disabled={botStatus.running}
          style={{ flex: 1, background: botStatus.running ? C.greenDim : C.green, border: "none", borderRadius: 8, padding: "9px 0", color: botStatus.running ? C.green : "#000", fontWeight: 800, fontSize: 11, cursor: botStatus.running ? "default" : "pointer", transition: "all 0.2s" }}>
          {botStatus.running ? "● RUNNING" : "▶ START BOT"}
        </button>
        <button onClick={onStop} disabled={!botStatus.running}
          style={{ flex: 1, background: botStatus.running ? C.red : C.redDim, border: "none", borderRadius: 8, padding: "9px 0", color: botStatus.running ? "#fff" : C.red, fontWeight: 800, fontSize: 11, cursor: botStatus.running ? "pointer" : "default", transition: "all 0.2s" }}>
          ■ STOP
        </button>
      </div>

      {botStatus.running && (
        <div style={{ padding: "8px 10px", background: "#00e67608", border: `1px solid ${C.green}20`, borderRadius: 7, fontSize: 10 }}>
          {[["PID", String(botStatus.pid)], ["Started", fmtTime(botStatus.startedAt ?? "")], ["Cycle", `#${botStatus.cycle}`]].map(([k, v]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
              <span style={{ color: C.textDim }}>{k}</span>
              <span style={{ color: k === "Cycle" ? C.green : C.text, fontFamily: "monospace", fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </div>
      )}
      {botStatus.error && (
        <div style={{ marginTop: 6, padding: "6px 8px", background: C.redDim, borderRadius: 6, fontSize: 10, color: C.red, wordBreak: "break-word" }}>{botStatus.error}</div>
      )}
    </PanelBox>
  );
}

// ─── PROVIDER HEALTH ──────────────────────────────────────────────────────────
function ProviderHealthPanel({ health }: { health?: ProviderHealth }) {
  return (
    <PanelBox title="PROVIDER HEALTH">
      {health ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {[
            { label: "Binance Futures", ok: health.binance, note: health.binance ? "Keys active" : "Add keys in Settings" },
            { label: "OpenRouter AI", ok: health.openRouter, note: health.openRouter ? "Connected" : "Add key → AI tab" },
            { label: "Groq AI", ok: health.groq, note: health.groq ? "Connected" : "Optional" },
            { label: "Ollama (local)", ok: health.ollama, note: health.ollama ? "Connected" : "Optional" },
          ].map(({ label, ok, note }) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ width: 6, height: 6, borderRadius: "50%", background: ok ? C.green : C.red, boxShadow: ok ? `0 0 5px ${C.green}` : "none", flexShrink: 0 }} />
              <span style={{ fontSize: 10, color: C.text, flex: 1 }}>{label}</span>
              <span style={{ fontSize: 9, color: C.textDim }}>{note}</span>
            </div>
          ))}
          <div style={{ marginTop: 4, padding: "5px 8px", borderRadius: 5, background: health.aiReady ? C.greenDim : C.amberDim, fontSize: 9, fontWeight: 700, color: health.aiReady ? C.green : C.amber }}>
            {health.aiReady ? "✓ AI Council Active" : "⚠ Add AI key to enable council"}
          </div>
        </div>
      ) : <div style={{ fontSize: 10, color: C.textDim }}>Checking providers...</div>}
    </PanelBox>
  );
}

// ─── SIGNAL SUMMARY ───────────────────────────────────────────────────────────
function SignalSummaryPanel({ marketData, rsi }: { marketData: MarketData | null; rsi: number }) {
  const c = marketData?.priceHistory?.slice(-1)[0];
  const trend = c ? (c.ema20 > c.ema50 ? "BULLISH" : "BEARISH") : null;
  const rsiColor = rsi > 70 ? C.red : rsi < 30 ? C.green : C.textMid;

  return (
    <PanelBox title="MARKET SIGNAL">
      {c ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {[
            ["Price", fmtPrice(marketData?.price ?? 0), C.text],
            ["24h Change", `${(marketData?.change24h ?? 0) >= 0 ? "+" : ""}${fmt(marketData?.change24h)}%`, (marketData?.change24h ?? 0) >= 0 ? C.green : C.red],
            ["24h High", fmtPrice(marketData?.high24h ?? 0), C.textMid],
            ["24h Low", fmtPrice(marketData?.low24h ?? 0), C.textMid],
            ["RSI(14)", fmt(rsi, 1), rsiColor],
            ["EMA20", fmtPrice(c.ema20), C.cyan],
            ["EMA50", fmtPrice(c.ema50), C.amber],
            ["Volume 24h", `$${fmt(marketData?.volume24h ?? 0, 0)}`, C.textMid],
          ].map(([k, v, col]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: 10, color: C.textDim }}>{k}</span>
              <span style={{ fontSize: 10, color: col ?? C.text, fontFamily: "monospace", fontWeight: 600 }}>{v}</span>
            </div>
          ))}
          {trend && (
            <div style={{ marginTop: 4, padding: "5px 8px", borderRadius: 5, background: trend === "BULLISH" ? C.greenDim : C.redDim, fontSize: 10, fontWeight: 700, color: trend === "BULLISH" ? C.green : C.red, textAlign: "center" }}>
              {trend === "BULLISH" ? "▲" : "▼"} {trend} TREND
            </div>
          )}
        </div>
      ) : <div style={{ fontSize: 10, color: C.textDim }}>Loading market data...</div>}
    </PanelBox>
  );
}

// ─── RISK PANEL ───────────────────────────────────────────────────────────────
function RiskPanel({ openTrades, closedTrades, settings }: {
  openTrades: OpenTrade[]; closedTrades: ClosedTrade[]; settings: Settings | null;
}) {
  const pnls = closedTrades.map((t) => parseFloat(t.pnl ?? "0")).filter((n) => !isNaN(n));
  const totalPnl = pnls.reduce((a, b) => a + b, 0);
  const wins = pnls.filter((n) => n > 0).length;
  const winRate = pnls.length ? Math.round(wins / pnls.length * 100) : 0;

  return (
    <PanelBox title="RISK & PORTFOLIO">
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {[
          ["Open Trades", String(openTrades.length), C.cyan],
          ["Closed Trades", String(closedTrades.length), C.textMid],
          ["Risk/Trade", `${settings?.settings["RISK_PERCENT"] ?? "1.0"}%`, C.amber],
          ["Reward:Risk", `${settings?.settings["REWARD_TO_RISK"] ?? "2.0"}:1`, C.textMid],
          ...(pnls.length ? [
            ["Total PnL", `${totalPnl >= 0 ? "+" : ""}${fmt(totalPnl)} USDT`, totalPnl >= 0 ? C.green : C.red],
            ["Win Rate", `${winRate}%`, winRate >= 50 ? C.green : C.red],
          ] : []),
        ].map(([k, v, col]) => (
          <div key={k} style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ fontSize: 10, color: C.textDim }}>{k}</span>
            <span style={{ fontSize: 10, color: col ?? C.text, fontFamily: "monospace", fontWeight: 600 }}>{v}</span>
          </div>
        ))}
      </div>
    </PanelBox>
  );
}

// ─── AI COUNCIL PANEL ─────────────────────────────────────────────────────────
const PODS = [
  { id: "trend",     label: "Trend Pod",     color: C.cyan,   icon: "📈" },
  { id: "structure", label: "Structure Pod", color: C.purple, icon: "🔲" },
  { id: "regime",    label: "Regime Pod",    color: C.amber,  icon: "🌊" },
  { id: "risk",      label: "Risk Pod",      color: C.red,    icon: "⚠"  },
  { id: "execution", label: "Exec Pod",      color: C.green,  icon: "⚡" },
  { id: "judge",     label: "Judge",         color: "#ba68c8",icon: "⚖"  },
];

function AICouncilPanel({ running, aiReady, activities }: {
  running: boolean; aiReady: boolean; activities: ActivityEntry[];
}) {
  if (!aiReady) {
    return (
      <div style={{ textAlign: "center", padding: "12px 0", color: C.textDim, fontSize: 10 }}>
        <div style={{ fontSize: 22, marginBottom: 6 }}>🤖</div>
        Add OpenRouter / Groq key in Settings → AI to enable council
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
      {PODS.map((pod) => {
        const recent = activities.find((a) =>
          a.msg.toLowerCase().includes(pod.id) || a.agent.toLowerCase().includes(pod.id)
        );
        const age = recent ? Date.now() - new Date(recent.ts).getTime() : Infinity;
        const status = !running ? "idle" : !recent ? "idle" : age < 8000 ? "active" : "done";

        return (
          <div key={pod.id} style={{ background: C.card, border: `1px solid ${status === "active" ? pod.color + "80" : C.border}`, borderRadius: 8, padding: "8px 9px", boxShadow: status === "active" ? `0 0 10px ${pod.color}20` : "none", transition: "all 0.3s" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 4 }}>
              <span style={{ fontSize: 11 }}>{pod.icon}</span>
              <span style={{ fontSize: 8, fontWeight: 700, color: pod.color, letterSpacing: 0.5, flex: 1 }}>{pod.label.toUpperCase()}</span>
              <div style={{ width: 5, height: 5, borderRadius: "50%", background: status === "active" ? pod.color : status === "done" ? pod.color + "60" : C.textDim + "40", boxShadow: status === "active" ? `0 0 5px ${pod.color}` : "none", animation: status === "active" ? "pulse 1s infinite" : "none" }} />
            </div>
            <div style={{ fontSize: 9, color: C.textDim, lineHeight: 1.4, minHeight: 20 }}>
              {recent && age < 60000 ? recent.msg.slice(0, 55) + (recent.msg.length > 55 ? "…" : "") : running ? "Monitoring…" : "Idle"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── PRICE CHART ──────────────────────────────────────────────────────────────
function PriceChart({ data }: { data: Candle[] }) {
  const recent = data.slice(-60).map((c) => ({
    ...c,
    t: fmtTime(c.time).slice(0, 5),
    bullish: c.close >= c.open,
  }));
  const prices = recent.flatMap((c) => [c.high, c.low]);
  const minP = prices.length ? Math.min(...prices) * 0.9985 : 0;
  const maxP = prices.length ? Math.max(...prices) * 1.0015 : 1;

  return (
    <div style={{ marginBottom: 2 }}>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={recent} margin={{ top: 4, right: 2, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="2 3" stroke={C.border} />
          <XAxis dataKey="t" tick={{ fill: C.textDim, fontSize: 8 }} axisLine={false} tickLine={false} interval={9} />
          <YAxis domain={[minP, maxP]} tick={{ fill: C.textDim, fontSize: 8 }} axisLine={false} tickLine={false} tickFormatter={fmtPrice} width={60} />
          <Tooltip
            contentStyle={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 10, padding: "6px 10px" }}
            labelStyle={{ color: C.textDim }} itemStyle={{ color: C.text }}
            formatter={(v: number) => [fmtPrice(v)]}
          />
          <ReferenceLine y={recent[recent.length - 1]?.close} stroke={C.textDim + "60"} strokeDasharray="3 3" strokeWidth={0.8} />
          <Line dataKey="ema20" stroke={C.cyan} dot={false} strokeWidth={1.2} name="EMA20" />
          <Line dataKey="ema50" stroke={C.amber} dot={false} strokeWidth={1.2} name="EMA50" />
          <Area type="monotone" dataKey="close" stroke={C.green} strokeWidth={1.5} fill={C.greenDim} dot={false} name="Price" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function RSIChart({ data }: { data: Candle[] }) {
  const recent = data.slice(-60).map((c) => ({ rsi: c.rsi, t: fmtTime(c.time).slice(0, 5) }));
  return (
    <div style={{ marginTop: 2 }}>
      <div style={{ fontSize: 8, color: C.textDim, letterSpacing: 1, marginBottom: 2, paddingLeft: 2 }}>RSI(14)</div>
      <ResponsiveContainer width="100%" height={54}>
        <ComposedChart data={recent} margin={{ top: 0, right: 2, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="2 3" stroke={C.border} />
          <XAxis dataKey="t" hide />
          <YAxis domain={[0, 100]} tick={{ fill: C.textDim, fontSize: 8 }} axisLine={false} tickLine={false} ticks={[30, 50, 70]} width={24} />
          <ReferenceLine y={70} stroke={C.red + "80"} strokeDasharray="3 3" strokeWidth={0.8} />
          <ReferenceLine y={30} stroke={C.green + "80"} strokeDasharray="3 3" strokeWidth={0.8} />
          <Line type="monotone" dataKey="rsi" stroke={C.purple} dot={false} strokeWidth={1.5} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── ORDER BOOK ───────────────────────────────────────────────────────────────
function OrderBookView({ orderBook }: { orderBook: OrderBook }) {
  const asks = [...(orderBook.asks ?? [])].sort((a, b) => b.price - a.price).slice(0, 14);
  const bids = [...(orderBook.bids ?? [])].sort((a, b) => b.price - a.price).slice(0, 14);
  const maxBid = Math.max(...bids.map((b) => b.qty), 0.0001);
  const maxAsk = Math.max(...asks.map((a) => a.qty), 0.0001);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
      <div>
        <div style={{ fontSize: 8, color: C.red, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>ASKS (SELL PRESSURE)</div>
        {asks.map((a, i) => (
          <div key={i} style={{ position: "relative", display: "flex", justifyContent: "space-between", fontSize: 10, padding: "2px 4px", fontFamily: "monospace" }}>
            <div style={{ position: "absolute", right: 0, top: 0, bottom: 0, background: C.redDim, width: `${Math.min((a.qty / maxAsk) * 100, 100)}%` }} />
            <span style={{ color: C.red, position: "relative" }}>{fmtPrice(a.price)}</span>
            <span style={{ color: C.textDim, position: "relative" }}>{a.qty.toFixed(3)}</span>
          </div>
        ))}
      </div>
      <div>
        <div style={{ fontSize: 8, color: C.green, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>BIDS (BUY PRESSURE)</div>
        {bids.map((b, i) => (
          <div key={i} style={{ position: "relative", display: "flex", justifyContent: "space-between", fontSize: 10, padding: "2px 4px", fontFamily: "monospace" }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, background: C.greenDim, width: `${Math.min((b.qty / maxBid) * 100, 100)}%` }} />
            <span style={{ color: C.green, position: "relative" }}>{fmtPrice(b.price)}</span>
            <span style={{ color: C.textDim, position: "relative" }}>{b.qty.toFixed(3)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── TRADE TABLE ──────────────────────────────────────────────────────────────
function TradeTable({ trades, type }: { trades: OpenTrade[] | ClosedTrade[]; type: "open" | "closed" }) {
  if (!trades.length) return (
    <div style={{ color: C.textDim, fontSize: 10, textAlign: "center", padding: "16px 0" }}>No {type} trades</div>
  );

  const th: React.CSSProperties = { fontSize: 8, color: C.textDim, fontWeight: 700, letterSpacing: 1, padding: "4px 6px", textAlign: "left", borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" };
  const td: React.CSSProperties = { fontSize: 10, padding: "4px 6px", fontFamily: "monospace", borderBottom: `1px solid ${C.border}10`, whiteSpace: "nowrap" };

  if (type === "open") {
    return (
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead><tr>
          {["SYMBOL","SIDE","ENTRY","SL","TP","QTY","OPENED"].map((h) => <th key={h} style={th}>{h}</th>)}
        </tr></thead>
        <tbody>
          {(trades as OpenTrade[]).map((t, i) => (
            <tr key={i}>
              <td style={{ ...td, color: C.text, fontWeight: 700 }}>{t.symbol}</td>
              <td style={{ ...td, color: t.side === "long" ? C.green : C.red, fontWeight: 700 }}>{(t.side ?? "").toUpperCase()}</td>
              <td style={{ ...td, color: C.textMid }}>{fmtPrice(Number(t.entry_price))}</td>
              <td style={{ ...td, color: C.red }}>{fmtPrice(Number(t.stop_loss))}</td>
              <td style={{ ...td, color: C.green }}>{fmtPrice(Number(t.take_profit))}</td>
              <td style={{ ...td, color: C.textMid }}>{Number(t.quantity).toFixed(4)}</td>
              <td style={{ ...td, color: C.textDim }}>{fmtDateTime(t.opened_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead><tr>
        {["SYMBOL","SIDE","ENTRY","EXIT","PNL","REASON","CLOSED"].map((h) => <th key={h} style={th}>{h}</th>)}
      </tr></thead>
      <tbody>
        {(trades as ClosedTrade[]).map((t, i) => {
          const pnl = parseFloat(t.pnl ?? "0");
          return (
            <tr key={i}>
              <td style={{ ...td, color: C.text, fontWeight: 700 }}>{t.symbol}</td>
              <td style={{ ...td, color: t.side === "long" ? C.green : C.red, fontWeight: 700 }}>{(t.side ?? "").toUpperCase()}</td>
              <td style={{ ...td, color: C.textMid }}>{t.entry_price}</td>
              <td style={{ ...td, color: C.textMid }}>{t.exit_price}</td>
              <td style={{ ...td, color: pnl >= 0 ? C.green : C.red, fontWeight: 700 }}>{pnl >= 0 ? "+" : ""}{fmt(pnl, 2)}</td>
              <td style={{ ...td, color: C.textDim }}>{t.reason ?? "—"}</td>
              <td style={{ ...td, color: C.textDim }}>{fmtDateTime(t.closed_at)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ─── SETTINGS MODAL ────────────────────────────────────────────────────────────
function SettingsModal({ form, onChange, onSave, onClose, saving, health, saveError }: {
  form: Record<string, string>; onChange: (k: string, v: string) => void;
  onSave: () => void; onClose: () => void; saving: boolean; health?: ProviderHealth; saveError?: string | null;
}) {
  const [tab, setTab] = useState<"exchange" | "ai" | "risk" | "models">("exchange");

  const Fld = ({ k, label, ph, pw }: { k: string; label: string; ph?: string; pw?: boolean }) => (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 9, color: C.textDim, marginBottom: 4, fontWeight: 700, letterSpacing: 0.5 }}>{label}</div>
      <input type={pw ? "password" : "text"} value={form[k] ?? ""} onChange={(e) => onChange(k, e.target.value)} placeholder={ph ?? k}
        style={{ width: "100%", background: C.card, border: `1px solid ${C.border2}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 11, outline: "none", fontFamily: "monospace" }} />
    </div>
  );

  const Sel = ({ k, label, opts }: { k: string; label: string; opts: [string, string][] }) => (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 9, color: C.textDim, marginBottom: 4, fontWeight: 700, letterSpacing: 0.5 }}>{label}</div>
      <select value={form[k] ?? opts[0][0]} onChange={(e) => onChange(k, e.target.value)}
        style={{ width: "100%", background: C.card, border: `1px solid ${C.border2}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 11, outline: "none" }}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );

  return (
    <Modal title="⚙ SYSTEM SETTINGS" onClose={onClose} width={620}>
      <div style={{ display: "flex", borderBottom: `1px solid ${C.border}`, padding: "0 16px", background: C.panel }}>
        {(["exchange", "ai", "risk", "models"] as const).map((t) => (
          <TabBtn key={t} active={tab === t} onClick={() => setTab(t)}>
            {t === "exchange" ? "🏦 EXCHANGE" : t === "ai" ? "🤖 AI" : t === "risk" ? "⚠ RISK" : "🧬 MODELS"}
          </TabBtn>
        ))}
      </div>

      <div style={{ padding: "16px", maxHeight: 440, overflow: "auto" }}>
        {tab === "exchange" && <>
          <div style={{ padding: "8px 10px", background: health?.binance ? C.greenDim : C.amberDim, borderRadius: 8, marginBottom: 14, fontSize: 10, color: health?.binance ? C.green : C.amber, fontWeight: 600 }}>
            {health?.binance ? "✓ Binance API keys are active and loaded" : "⚠ Add Binance Futures Testnet keys below to enable live trading"}
          </div>
          <Fld k="BINANCE_API_KEY" label="BINANCE FUTURES TESTNET API KEY" ph="Your Binance Testnet API Key" pw />
          <Fld k="BINANCE_API_SECRET" label="BINANCE FUTURES TESTNET SECRET" ph="Your Binance Testnet Secret Key" pw />
          <Fld k="SYMBOLS" label="TRADING SYMBOLS (comma-separated)" ph="BTCUSDT,ETHUSDT,SOLUSDT" />
          <div style={{ padding: "8px 10px", background: "#42a5f510", borderRadius: 6, fontSize: 10, color: C.textMid, lineHeight: 1.6 }}>
            💡 Free testnet API keys: visit <span style={{ color: C.cyan }}>testnet.binancefuture.com</span> → log in → click "API Key" in top menu.
            These trade with demo money, not real funds.
          </div>
        </>}

        {tab === "ai" && <>
          <div style={{ padding: "8px 10px", background: health?.aiReady ? C.greenDim : C.amberDim, borderRadius: 8, marginBottom: 14, fontSize: 10, color: health?.aiReady ? C.green : C.amber, fontWeight: 600 }}>
            {health?.aiReady ? "✓ AI provider connected — council is active" : "⚠ Add an API key below to enable the AI council"}
          </div>
          <Fld k="OPENROUTER_API_KEY" label="OPENROUTER API KEY (recommended — one key powers many models)" ph="sk-or-v1-..." pw />
          <Fld k="GROQ_API_KEY" label="GROQ API KEY (optional — very fast inference)" ph="gsk_..." pw />
          <Fld k="OLLAMA_BASE_URL" label="OLLAMA BASE URL (optional — local models)" ph="http://localhost:11434" />
          <Sel k="AI_ENABLED" label="AI ENABLED" opts={[["true","Enabled (AI council reviews trades"],["false","Disabled (rule-based only)"]]} />
          <Sel k="AI_ORCHESTRATION_MODE" label="ORCHESTRATION MODE" opts={[["auto","Auto — score-based routing"],["light","Light — core pods only"],["standard","Standard — all pods"],["full","Full — all pods + judge"]]} />
          <Fld k="AI_DEFAULT_PROVIDER" label="DEFAULT PROVIDER" ph="openrouter" />
          <Fld k="AI_DEFAULT_MODEL" label="DEFAULT MODEL" ph="meta-llama/llama-3.1-8b-instruct" />
        </>}

        {tab === "risk" && <>
          <Fld k="RISK_PERCENT" label="RISK PER TRADE (%)" ph="1.0" />
          <Fld k="REWARD_TO_RISK" label="REWARD : RISK RATIO" ph="2.0" />
          <Fld k="VOLUME_RATIO_THRESHOLD" label="MIN VOLUME RATIO (vs avg)" ph="1.5" />
          <Fld k="REGIME_MIN_TREND_SCORE" label="MIN REGIME TREND SCORE (0.0 – 1.0)" ph="0.5" />
          <Fld k="LOSS_COOLDOWN_CANDLES" label="LOSS COOLDOWN (15m candles)" ph="3" />
          <Fld k="WIN_COOLDOWN_CANDLES" label="WIN COOLDOWN (15m candles)" ph="1" />
          <Fld k="MAX_TRADES_PER_WINDOW" label="MAX TRADES PER WINDOW" ph="3" />
        </>}

        {tab === "models" && <>
          <div style={{ padding: "8px 10px", background: "#42a5f510", borderRadius: 6, fontSize: 10, color: C.textMid, marginBottom: 14, lineHeight: 1.6 }}>
            Each pod uses 2 models for consensus. Providers: <code style={{ color: C.cyan }}>openrouter</code>, <code style={{ color: C.cyan }}>groq</code>, <code style={{ color: C.cyan }}>ollama</code>
          </div>
          {[
            { name: "TREND POD", prefix: "TREND_POD", color: C.cyan },
            { name: "STRUCTURE POD", prefix: "STRUCTURE_POD", color: C.purple },
            { name: "REGIME POD", prefix: "REGIME_POD", color: C.amber },
            { name: "RISK POD", prefix: "RISK_POD", color: C.red },
            { name: "EXECUTION POD", prefix: "EXECUTION_POD", color: C.green },
          ].map(({ name, prefix, color }) => (
            <div key={prefix} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 9, color, fontWeight: 700, marginBottom: 8, letterSpacing: 1 }}>{name}</div>
              <Fld k={`${prefix}_MODEL_A`} label="Model A" ph="meta-llama/llama-3.1-8b-instruct" />
              <Fld k={`${prefix}_MODEL_B`} label="Model B" ph="mistralai/mistral-7b-instruct" />
            </div>
          ))}
          <div style={{ fontSize: 9, color: "#ba68c8", fontWeight: 700, marginBottom: 8, letterSpacing: 1 }}>JUDGE AGENT</div>
          <Fld k="JUDGE_AGENT_MODEL" label="Judge Model" ph="anthropic/claude-3-haiku" />
          <Fld k="JUDGE_AGENT_PROVIDER" label="Judge Provider" ph="openrouter" />
        </>}
      </div>

      <div style={{ padding: "12px 16px", borderTop: `1px solid ${C.border}` }}>
        {saveError && (
          <div style={{ marginBottom: 10, padding: "8px 12px", background: C.redDim, border: `1px solid ${C.red}40`, borderRadius: 6, fontSize: 10, color: C.red, fontFamily: "monospace", wordBreak: "break-all" }}>
            ❌ {saveError}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Btn onClick={onClose}>Cancel</Btn>
          <Btn primary onClick={onSave} disabled={saving}>{saving ? "Saving..." : "💾 Save Settings"}</Btn>
        </div>
      </div>
    </Modal>
  );
}

// ─── Shared primitives ─────────────────────────────────────────────────────────
function PanelBox({ title, accent, children }: { title: string; accent?: string; children: React.ReactNode }) {
  return (
    <div style={{ borderBottom: `1px solid ${C.border}` }}>
      <div style={{ padding: "8px 12px 4px", display: "flex", alignItems: "center", gap: 6 }}>
        {accent && <div style={{ width: 2, height: 10, borderRadius: 1, background: accent, flexShrink: 0 }} />}
        <span style={{ fontSize: 8, fontWeight: 700, letterSpacing: 1.5, color: C.textDim }}>{title}</span>
      </div>
      <div style={{ padding: "2px 12px 10px" }}>{children}</div>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 8, fontWeight: 700, letterSpacing: 1.5, color: C.textDim, marginBottom: 8 }}>{children}</div>;
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{ background: "transparent", border: "none", padding: "4px 10px", cursor: "pointer", fontSize: 9, fontWeight: 700, letterSpacing: 0.8, color: active ? C.green : C.textDim, borderBottom: active ? `2px solid ${C.green}` : "2px solid transparent", marginBottom: -1, whiteSpace: "nowrap" }}>
      {children}
    </button>
  );
}

function Modal({ title, onClose, width = 600, children }: {
  title: string; onClose: () => void; width?: number; children: React.ReactNode;
}) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "#00000088", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: 20 }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, width, maxWidth: "95vw", maxHeight: "92vh", overflow: "auto", display: "flex", flexDirection: "column", boxShadow: "0 24px 80px #000000c0" }}>
        <div style={{ padding: "14px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
          <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: 1.5, color: C.green }}>{title}</span>
          <button onClick={onClose} style={{ background: "transparent", border: "none", color: C.textDim, fontSize: 20, cursor: "pointer", lineHeight: 1, padding: "0 4px" }}>×</button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Btn({ onClick, children, primary, disabled }: {
  onClick: () => void; children: React.ReactNode; primary?: boolean; disabled?: boolean;
}) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{ padding: "8px 20px", borderRadius: 8, border: primary ? "none" : `1px solid ${C.border}`, background: primary ? (disabled ? C.greenDim : C.green) : "transparent", color: primary ? "#000" : C.textMid, fontSize: 11, fontWeight: primary ? 700 : 400, cursor: disabled ? "wait" : "pointer", minWidth: primary ? 130 : undefined }}>
      {children}
    </button>
  );
}
