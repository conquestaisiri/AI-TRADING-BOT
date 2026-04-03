import { Router } from "express";

const router = Router();

interface BotState {
  running: boolean;
  pair: string | null;
  startedAt: string | null;
  cycle: number;
  balance: number;
  equity: number;
  totalPnl: number;
  winRate: number;
  trades: TradeRecord[];
  closedTrades: TradeRecord[];
  equityCurve: EquityPoint[];
  lastPrice: number;
  priceHistory: PriceCandle[];
  agents: AgentStatus[];
}

interface TradeRecord {
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

interface EquityPoint {
  time: string;
  equity: number;
  pnl: number;
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

interface AgentStatus {
  id: string;
  name: string;
  stage: number;
  status: "idle" | "active" | "done" | "skip" | "warn";
  lastAction: string;
  calls: number;
}

const PAIRS: Record<string, number> = {
  BTCUSDT: 84250,
  ETHUSDT: 1620,
  SOLUSDT: 138,
  BNBUSDT: 595,
  XRPUSDT: 2.18,
  DOGEUSDT: 0.168,
  ADAUSDT: 0.72,
  AVAXUSDT: 22.5,
};

function randBetween(a: number, b: number) {
  return a + Math.random() * (b - a);
}

function generatePriceHistory(basePrice: number, count = 120): PriceCandle[] {
  const candles: PriceCandle[] = [];
  let price = basePrice * 0.985;
  const ema20Arr: number[] = [];
  const ema50Arr: number[] = [];

  const k20 = 2 / (20 + 1);
  const k50 = 2 / (50 + 1);

  let ema20 = price;
  let ema50 = price;

  const now = Date.now();

  for (let i = count; i >= 0; i--) {
    const change = randBetween(-0.012, 0.013);
    const open = price;
    price = price * (1 + change);
    const high = Math.max(open, price) * (1 + randBetween(0, 0.004));
    const low = Math.min(open, price) * (1 - randBetween(0, 0.004));
    const close = price;
    const volume = randBetween(400, 1800);

    ema20 = close * k20 + ema20 * (1 - k20);
    ema50 = close * k50 + ema50 * (1 - k50);
    ema20Arr.push(ema20);
    ema50Arr.push(ema50);

    const rsi = 40 + 30 * Math.sin(i * 0.15) + randBetween(-8, 8);

    candles.push({
      time: new Date(now - i * 15 * 60 * 1000).toISOString(),
      open: +open.toFixed(2),
      high: +high.toFixed(2),
      low: +low.toFixed(2),
      close: +close.toFixed(2),
      volume: +volume.toFixed(0),
      ema20: +ema20.toFixed(2),
      ema50: +ema50.toFixed(2),
      rsi: +Math.max(20, Math.min(80, rsi)).toFixed(1),
    });
  }
  return candles;
}

function generateEquityCurve(startBalance: number, count = 60): EquityPoint[] {
  const points: EquityPoint[] = [];
  let eq = startBalance;
  const now = Date.now();
  for (let i = count; i >= 0; i--) {
    const delta = randBetween(-180, 250);
    eq = Math.max(startBalance * 0.85, eq + delta);
    points.push({
      time: new Date(now - i * 60 * 60 * 1000).toISOString(),
      equity: +eq.toFixed(2),
      pnl: +(eq - startBalance).toFixed(2),
    });
  }
  return points;
}

const botState: BotState = {
  running: false,
  pair: null,
  startedAt: null,
  cycle: 0,
  balance: 10000,
  equity: 10000,
  totalPnl: 0,
  winRate: 0,
  trades: [],
  closedTrades: [],
  equityCurve: generateEquityCurve(10000),
  lastPrice: 0,
  priceHistory: [],
  agents: [
    { id: "market-analyst", name: "Market Analyst", stage: 1, status: "idle", lastAction: "Awaiting pair selection", calls: 0 },
    { id: "data-fetcher", name: "Data Fetcher", stage: 2, status: "idle", lastAction: "Standby", calls: 0 },
    { id: "indicator-engine", name: "Indicator Engine", stage: 3, status: "idle", lastAction: "Standby", calls: 0 },
    { id: "regime-classifier", name: "Regime Classifier", stage: 4, status: "idle", lastAction: "Standby", calls: 0 },
    { id: "signal-generator", name: "Signal Generator", stage: 5, status: "idle", lastAction: "Standby", calls: 0 },
    { id: "risk-manager", name: "Risk Manager", stage: 6, status: "idle", lastAction: "Standby", calls: 0 },
    { id: "order-executor", name: "Order Executor", stage: 7, status: "idle", lastAction: "Standby", calls: 0 },
  ],
};

export const activityBus: { listeners: ((msg: string) => void)[] } = {
  listeners: [],
};

function emit(msg: string) {
  activityBus.listeners.forEach((fn) => fn(msg));
}

function updateAgent(id: string, patch: Partial<AgentStatus>) {
  const agent = botState.agents.find((a) => a.id === id);
  if (agent) Object.assign(agent, patch);
}

let cycleTimer: ReturnType<typeof setTimeout> | null = null;

async function runCycle() {
  if (!botState.running) return;

  const pair = botState.pair!;
  const basePrice = PAIRS[pair] ?? 100;
  botState.cycle++;
  const cycle = botState.cycle;

  emit(JSON.stringify({ type: "cycle_start", cycle, pair, ts: new Date().toISOString() }));

  updateAgent("market-analyst", { status: "active", lastAction: `Analyzing ${pair} market structure...`, calls: (botState.agents[0].calls || 0) + 1 });
  emit(JSON.stringify({ type: "log", level: "info", agent: "Market Analyst", msg: `[Cycle ${cycle}] Starting analysis for ${pair} — fetching market regime...`, ts: new Date().toISOString() }));

  await sleep(600);

  updateAgent("data-fetcher", { status: "active", lastAction: `Fetching 300 candles (1H + 15M)`, calls: (botState.agents[1].calls || 0) + 1 });
  emit(JSON.stringify({ type: "log", level: "info", agent: "Data Fetcher", msg: `Fetching OHLCV: 300 × 1H candles + 300 × 15M candles for ${pair}`, ts: new Date().toISOString() }));
  await sleep(400);

  const newCandle: PriceCandle = {
    time: new Date().toISOString(),
    open: basePrice * (1 + randBetween(-0.003, 0.003)),
    high: basePrice * (1 + randBetween(0.001, 0.006)),
    low: basePrice * (1 - randBetween(0.001, 0.006)),
    close: basePrice * (1 + randBetween(-0.004, 0.005)),
    volume: +randBetween(400, 1600).toFixed(0),
    ema20: basePrice * (1 - randBetween(0.001, 0.004)),
    ema50: basePrice * (1 - randBetween(0.003, 0.007)),
    rsi: +randBetween(35, 65).toFixed(1),
  };
  botState.priceHistory.push(newCandle);
  if (botState.priceHistory.length > 200) botState.priceHistory.shift();
  botState.lastPrice = newCandle.close;

  updateAgent("data-fetcher", { status: "done", lastAction: `Loaded 300 candles ✓` });
  emit(JSON.stringify({ type: "price_update", pair, price: newCandle.close, candle: newCandle, ts: new Date().toISOString() }));

  updateAgent("indicator-engine", { status: "active", lastAction: `Computing EMA20/50, RSI14, ATR14...`, calls: (botState.agents[2].calls || 0) + 1 });
  emit(JSON.stringify({ type: "log", level: "info", agent: "Indicator Engine", msg: `Computing indicators — EMA20: ${newCandle.ema20.toFixed(2)} | EMA50: ${newCandle.ema50.toFixed(2)} | RSI: ${newCandle.rsi} | ATR: ${(basePrice * 0.0085).toFixed(2)}`, ts: new Date().toISOString() }));
  await sleep(300);
  updateAgent("indicator-engine", { status: "done", lastAction: `EMA20=${newCandle.ema20.toFixed(2)}, RSI=${newCandle.rsi}` });

  const emaSpread = ((newCandle.ema20 - newCandle.ema50) / newCandle.ema50) * 100;
  const isTrending = Math.abs(emaSpread) > 0.3;
  const regimeScore = +randBetween(0.45, 0.85).toFixed(2);
  const regime = regimeScore > 0.6 ? "TRENDING" : regimeScore > 0.45 ? "RANGING" : "CHOPPY";

  updateAgent("regime-classifier", { status: "active", lastAction: `Scoring market regime...`, calls: (botState.agents[3].calls || 0) + 1 });
  emit(JSON.stringify({ type: "log", level: regime === "TRENDING" ? "info" : "warn", agent: "Regime Classifier", msg: `Market Regime: ${regime} | Score: ${regimeScore} | EMA spread: ${emaSpread.toFixed(3)}% | ${regimeScore >= 0.5 ? "✓ Passes threshold (≥0.50)" : "✗ Below threshold — signal BLOCKED"}`, ts: new Date().toISOString() }));
  await sleep(250);
  updateAgent("regime-classifier", { status: regime === "TRENDING" ? "done" : "skip", lastAction: `${regime} (score=${regimeScore})` });

  emit(JSON.stringify({ type: "regime", regime, score: regimeScore, ts: new Date().toISOString() }));

  if (regimeScore < 0.5) {
    emit(JSON.stringify({ type: "log", level: "warn", agent: "Signal Generator", msg: `Signal evaluation SKIPPED — regime score ${regimeScore} below minimum 0.50`, ts: new Date().toISOString() }));
    updateAgent("signal-generator", { status: "skip", lastAction: "Skipped — regime filter failed" });
    updateAgent("risk-manager", { status: "skip", lastAction: "Skipped" });
    updateAgent("order-executor", { status: "skip", lastAction: "Skipped" });
    emit(JSON.stringify({ type: "cycle_end", cycle, result: "no_signal", ts: new Date().toISOString() }));
    scheduleNextCycle();
    return;
  }

  updateAgent("signal-generator", { status: "active", lastAction: "Running 7-stage signal evaluation...", calls: (botState.agents[4].calls || 0) + 1 });

  const volumeRatio = +randBetween(0.8, 2.2).toFixed(2);
  const hasBreakout = randBetween(0, 1) > 0.55;
  const direction = newCandle.ema20 > newCandle.ema50 ? "LONG" : "SHORT";

  emit(JSON.stringify({ type: "log", level: "info", agent: "Signal Generator", msg: `Stage 2 (Trend): EMA20 ${newCandle.ema20 > newCandle.ema50 ? "ABOVE" : "BELOW"} EMA50 → ${direction} bias`, ts: new Date().toISOString() }));
  await sleep(200);
  emit(JSON.stringify({ type: "log", level: volumeRatio >= 1.5 ? "info" : "warn", agent: "Signal Generator", msg: `Stage 5 (Volume): ${volumeRatio}x avg — ${volumeRatio >= 1.5 ? "✓ Passes (≥1.5x)" : "✗ Insufficient volume"}`, ts: new Date().toISOString() }));
  await sleep(150);
  emit(JSON.stringify({ type: "log", level: hasBreakout ? "signal" : "info", agent: "Signal Generator", msg: `Stage 4 (Breakout): ${hasBreakout ? `✓ Breakout detected — close above swing high by ${randBetween(0.1, 0.5).toFixed(2)}x ATR` : "✗ No breakout candidate"}`, ts: new Date().toISOString() }));
  await sleep(200);

  const hasSignal = hasBreakout && volumeRatio >= 1.5 && newCandle.rsi < 70 && newCandle.rsi > 30;
  updateAgent("signal-generator", { status: hasSignal ? "done" : "skip", lastAction: hasSignal ? `${direction} signal approved` : "No signal this cycle" });

  if (!hasSignal) {
    emit(JSON.stringify({ type: "log", level: "info", agent: "Signal Generator", msg: `No actionable signal this cycle — all checks incomplete`, ts: new Date().toISOString() }));
    updateAgent("risk-manager", { status: "skip", lastAction: "No signal to size" });
    updateAgent("order-executor", { status: "skip", lastAction: "Nothing to execute" });
    emit(JSON.stringify({ type: "cycle_end", cycle, result: "no_signal", ts: new Date().toISOString() }));
    scheduleNextCycle();
    return;
  }

  emit(JSON.stringify({ type: "log", level: "signal", agent: "Signal Generator", msg: `🎯 SIGNAL APPROVED — ${direction} ${pair} | RSI=${newCandle.rsi} | Volume=${volumeRatio}x | Regime=${regime}`, ts: new Date().toISOString() }));

  updateAgent("risk-manager", { status: "active", lastAction: `Sizing ${direction} position...`, calls: (botState.agents[5].calls || 0) + 1 });
  const atr = basePrice * 0.0085;
  const slPct = 1.5 * atr;
  const tpPct = slPct * 2.0;
  const riskAmt = botState.balance * 0.01;
  const positionSize = +(riskAmt / slPct).toFixed(4);
  const entryPrice = newCandle.close;
  const sl = direction === "LONG" ? entryPrice - slPct : entryPrice + slPct;
  const tp = direction === "LONG" ? entryPrice + tpPct : entryPrice - tpPct;

  emit(JSON.stringify({ type: "log", level: "info", agent: "Risk Manager", msg: `Risk calc: ATR=${atr.toFixed(2)} | SL=${sl.toFixed(2)} | TP=${tp.toFixed(2)} | Size=${positionSize} | RiskAmt=$${riskAmt.toFixed(2)} (1%)`, ts: new Date().toISOString() }));
  await sleep(300);
  updateAgent("risk-manager", { status: "done", lastAction: `Size=${positionSize} | Risk=$${riskAmt.toFixed(2)}` });

  updateAgent("order-executor", { status: "active", lastAction: `Placing ${direction} market order...`, calls: (botState.agents[6].calls || 0) + 1 });
  emit(JSON.stringify({ type: "log", level: "order", agent: "Order Executor", msg: `Placing MARKET ${direction} — ${pair} @ ${entryPrice.toFixed(2)} | Size: ${positionSize} | SL: ${sl.toFixed(2)} | TP: ${tp.toFixed(2)}`, ts: new Date().toISOString() }));
  await sleep(400);

  const newTrade: TradeRecord = {
    id: `T${Date.now()}`,
    symbol: pair,
    side: direction,
    entry: +entryPrice.toFixed(2),
    size: positionSize,
    sl: +sl.toFixed(2),
    tp: +tp.toFixed(2),
    pnl: 0,
    status: "OPEN",
    openedAt: new Date().toISOString(),
  };
  botState.trades.push(newTrade);

  emit(JSON.stringify({ type: "trade_open", trade: newTrade, ts: new Date().toISOString() }));
  emit(JSON.stringify({ type: "log", level: "order", agent: "Order Executor", msg: `✅ Order FILLED — Trade ${newTrade.id} | ${direction} ${pair} @ ${entryPrice.toFixed(2)}`, ts: new Date().toISOString() }));
  updateAgent("order-executor", { status: "done", lastAction: `Filled ${direction} @ ${entryPrice.toFixed(2)}` });

  if (Math.random() > 0.5 && botState.trades.length > 0) {
    const closeTrade = botState.trades.find((t) => t.status === "OPEN" && t.id !== newTrade.id);
    if (closeTrade) {
      const outcome = Math.random() > 0.45;
      const exitPrice = outcome
        ? direction === "LONG" ? closeTrade.tp : closeTrade.sl
        : direction === "LONG" ? closeTrade.sl : closeTrade.tp;
      const pnl = outcome ? closeTrade.size * (closeTrade.tp - closeTrade.entry) : -closeTrade.size * (closeTrade.entry - closeTrade.sl);
      closeTrade.status = "CLOSED";
      closeTrade.pnl = +pnl.toFixed(2);
      closeTrade.closedAt = new Date().toISOString();
      closeTrade.closeReason = outcome ? "TP HIT" : "SL HIT";
      botState.closedTrades.push(closeTrade);
      botState.trades = botState.trades.filter((t) => t.id !== closeTrade.id);
      botState.totalPnl += closeTrade.pnl;
      botState.balance += closeTrade.pnl;
      botState.equity = botState.balance;

      const wins = botState.closedTrades.filter((t) => t.pnl > 0).length;
      botState.winRate = botState.closedTrades.length > 0 ? (wins / botState.closedTrades.length) * 100 : 0;

      botState.equityCurve.push({
        time: new Date().toISOString(),
        equity: botState.equity,
        pnl: botState.totalPnl,
      });

      emit(JSON.stringify({ type: "trade_close", trade: closeTrade, pnl: closeTrade.pnl, ts: new Date().toISOString() }));
      emit(JSON.stringify({ type: "log", level: outcome ? "profit" : "loss", agent: "Order Executor", msg: `Trade ${closeTrade.id} CLOSED — ${closeTrade.closeReason} | P&L: ${closeTrade.pnl >= 0 ? "+" : ""}${closeTrade.pnl.toFixed(2)} USDT`, ts: new Date().toISOString() }));
    }
  }

  emit(JSON.stringify({ type: "cycle_end", cycle, result: "executed", ts: new Date().toISOString() }));
  scheduleNextCycle();
}

function scheduleNextCycle() {
  if (!botState.running) return;
  cycleTimer = setTimeout(runCycle, 8000);
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

router.get("/status", (_req, res) => {
  res.json({
    running: botState.running,
    pair: botState.pair,
    startedAt: botState.startedAt,
    cycle: botState.cycle,
    balance: botState.balance,
    equity: botState.equity,
    totalPnl: botState.totalPnl,
    winRate: botState.winRate,
    openTrades: botState.trades.length,
    closedTrades: botState.closedTrades.length,
    lastPrice: botState.lastPrice,
    agents: botState.agents,
  });
});

router.post("/start", (req, res) => {
  const { pair } = req.body as { pair: string };
  if (!pair) return res.status(400).json({ error: "pair required" });
  if (botState.running) return res.status(409).json({ error: "Bot already running" });

  const basePrice = PAIRS[pair] ?? 100;
  botState.running = true;
  botState.pair = pair;
  botState.startedAt = new Date().toISOString();
  botState.cycle = 0;
  botState.lastPrice = basePrice;
  botState.priceHistory = generatePriceHistory(basePrice);
  botState.agents.forEach((a) => { a.status = "idle"; a.lastAction = "Initializing..."; a.calls = 0; });

  emit(JSON.stringify({ type: "system", msg: `🚀 Trading system ACTIVATED — pair: ${pair}`, ts: new Date().toISOString() }));
  emit(JSON.stringify({ type: "log", level: "system", agent: "System", msg: `Bot initialized for ${pair} | Balance: $${botState.balance.toFixed(2)} USDT | Starting cycle engine...`, ts: new Date().toISOString() }));

  scheduleNextCycle();
  res.json({ ok: true, pair, message: `Trading system activated for ${pair}` });
});

router.post("/stop", (_req, res) => {
  botState.running = false;
  botState.pair = null;
  if (cycleTimer) { clearTimeout(cycleTimer); cycleTimer = null; }
  botState.agents.forEach((a) => { a.status = "idle"; a.lastAction = "System stopped"; });
  emit(JSON.stringify({ type: "system", msg: "⏹ Trading system STOPPED", ts: new Date().toISOString() }));
  res.json({ ok: true });
});

router.get("/market/:pair", (req, res) => {
  const { pair } = req.params;
  const base = PAIRS[pair] ?? 100;
  const drift = randBetween(-0.002, 0.002);
  if (PAIRS[pair]) PAIRS[pair] = PAIRS[pair] * (1 + drift);

  res.json({
    pair,
    price: +(base * (1 + drift)).toFixed(2),
    change24h: +randBetween(-4.5, 5.2).toFixed(2),
    volume24h: +randBetween(1e8, 4e9).toFixed(0),
    high24h: +(base * 1.025).toFixed(2),
    low24h: +(base * 0.975).toFixed(2),
    priceHistory: botState.priceHistory,
  });
});

router.get("/trades", (_req, res) => {
  res.json({
    open: botState.trades,
    closed: botState.closedTrades.slice(-50),
    equityCurve: botState.equityCurve,
  });
});

router.get("/pairs", (_req, res) => {
  res.json(Object.keys(PAIRS));
});

export default router;
