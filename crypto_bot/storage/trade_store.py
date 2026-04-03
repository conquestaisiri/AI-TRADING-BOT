import csv
import os
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Literal
from logs.logger import get_logger

logger = get_logger("storage.trade_store")

STORAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(STORAGE_DIR, "trades.db")
CLOSED_CSV_PATH = os.path.join(STORAGE_DIR, "closed_trades.csv")

TradeStatus = Literal["open", "closed_sl", "closed_tp", "closed_manual"]
TradeDirection = Literal["long", "short"]


@dataclass
class Trade:
    id: str
    symbol: str
    direction: TradeDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    risk_amount_usdt: float
    reward_amount_usdt: float
    risk_distance: float
    atr: float
    candle_timestamp: str
    trend_1h: str
    regime_label: str
    regime_score: float
    opened_at: str
    status: TradeStatus = "open"
    closed_at: str = ""
    close_price: float = 0.0
    pnl_usdt: float = 0.0


class TradeStore:
    """
    Persistent trade state via SQLite (open trades) and CSV (closed trade audit log).

    Also provides query methods for cooldown and trade frequency logic:
      - get_last_closed_trade()
      - get_recent_entry_times()
      - get_recent_closed_trades()
    """

    def __init__(self, db_path: str = DB_PATH, csv_path: str = CLOSED_CSV_PATH):
        self.db_path = db_path
        self.csv_path = csv_path
        self._init_db()
        self._init_csv()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS open_trades (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    quantity REAL NOT NULL,
                    risk_amount_usdt REAL NOT NULL,
                    reward_amount_usdt REAL NOT NULL,
                    risk_distance REAL NOT NULL,
                    atr REAL NOT NULL,
                    candle_timestamp TEXT NOT NULL,
                    trend_1h TEXT NOT NULL,
                    regime_label TEXT NOT NULL DEFAULT '',
                    regime_score REAL NOT NULL DEFAULT 0.0,
                    opened_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    closed_at TEXT DEFAULT '',
                    close_price REAL DEFAULT 0.0,
                    pnl_usdt REAL DEFAULT 0.0
                )
            """)
            # Attempt to add new columns if upgrading from older schema
            for col_sql in [
                "ALTER TABLE open_trades ADD COLUMN regime_label TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE open_trades ADD COLUMN regime_score REAL NOT NULL DEFAULT 0.0",
            ]:
                try:
                    conn.execute(col_sql)
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.commit()
        logger.debug("SQLite trade DB ready: %s", self.db_path)

    def _init_csv(self) -> None:
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(Trade.__dataclass_fields__.keys()))
                writer.writeheader()
            logger.debug("Closed trades CSV created: %s", self.csv_path)

    def save_open_trade(self, trade: Trade) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO open_trades
                (id, symbol, direction, entry_price, stop_loss, take_profit,
                 quantity, risk_amount_usdt, reward_amount_usdt, risk_distance,
                 atr, candle_timestamp, trend_1h, regime_label, regime_score,
                 opened_at, status, closed_at, close_price, pnl_usdt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.id, trade.symbol, trade.direction,
                    trade.entry_price, trade.stop_loss, trade.take_profit,
                    trade.quantity, trade.risk_amount_usdt, trade.reward_amount_usdt,
                    trade.risk_distance, trade.atr,
                    trade.candle_timestamp, trade.trend_1h,
                    trade.regime_label, trade.regime_score,
                    trade.opened_at, trade.status, trade.closed_at,
                    trade.close_price, trade.pnl_usdt,
                ),
            )
            conn.commit()
        logger.debug("Trade saved: %s %s %s", trade.id, trade.symbol, trade.direction)

    def load_open_trades(self) -> list[Trade]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM open_trades WHERE status = 'open'"
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def _row_to_trade(self, row: tuple) -> Trade:
        return Trade(
            id=row[0], symbol=row[1], direction=row[2],
            entry_price=row[3], stop_loss=row[4], take_profit=row[5],
            quantity=row[6], risk_amount_usdt=row[7], reward_amount_usdt=row[8],
            risk_distance=row[9], atr=row[10],
            candle_timestamp=row[11], trend_1h=row[12],
            regime_label=row[13], regime_score=row[14],
            opened_at=row[15], status=row[16], closed_at=row[17],
            close_price=row[18], pnl_usdt=row[19],
        )

    def has_open_trade_for_symbol(self, symbol: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM open_trades WHERE symbol = ? AND status = 'open' LIMIT 1",
                (symbol,),
            ).fetchone()
        return row is not None

    def close_trade(self, trade: Trade) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE open_trades
                SET status = ?, closed_at = ?, close_price = ?, pnl_usdt = ?
                WHERE id = ?
                """,
                (trade.status, trade.closed_at, trade.close_price, trade.pnl_usdt, trade.id),
            )
            conn.commit()
        self._append_closed_csv(trade)
        logger.info(
            "TRADE CLOSED | id=%s | %s %s | entry=%.4f close=%.4f | "
            "PnL=%.2f USDT | reason=%s",
            trade.id, trade.symbol, trade.direction,
            trade.entry_price, trade.close_price, trade.pnl_usdt, trade.status,
        )

    def _append_closed_csv(self, trade: Trade) -> None:
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(Trade.__dataclass_fields__.keys()))
            writer.writerow(asdict(trade))

    # ── Cooldown and frequency query methods ──────────────────────────────────

    def get_last_closed_trade(self, symbol: str) -> Trade | None:
        """
        Return the most recently closed trade for this symbol (by closed_at).
        Returns None if no closed trade exists.
        Used to determine cooldown after a win or loss.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM open_trades
                WHERE symbol = ? AND status IN ('closed_sl', 'closed_tp', 'closed_manual')
                ORDER BY closed_at DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        return self._row_to_trade(row) if row else None

    def get_recent_entry_times(self, symbol: str, since: datetime) -> list[datetime]:
        """
        Return a list of opened_at datetimes for trades on this symbol
        that were entered since `since`. Used for frequency limiting.
        """
        since_str = since.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT opened_at FROM open_trades
                WHERE symbol = ? AND opened_at >= ?
                ORDER BY opened_at ASC
                """,
                (symbol, since_str),
            ).fetchall()

        result: list[datetime] = []
        for (ts_str,) in rows:
            try:
                result.append(datetime.fromisoformat(ts_str))
            except ValueError:
                pass
        return result

    def get_recent_closed_trades(self, symbol: str, since: datetime) -> list[Trade]:
        """
        Return closed trades for this symbol since `since`.
        Used for frequency and loss-streak analysis.
        """
        since_str = since.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM open_trades
                WHERE symbol = ? AND status IN ('closed_sl', 'closed_tp', 'closed_manual')
                  AND closed_at >= ?
                ORDER BY closed_at ASC
                """,
                (symbol, since_str),
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def get_all_open_trades(self) -> list[Trade]:
        return self.load_open_trades()
