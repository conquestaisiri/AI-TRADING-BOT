import csv
import os
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Literal
from logs.logger import get_logger

logger = get_logger("storage.trade_store")

STORAGE_DIR = os.path.join(os.path.dirname(__file__))
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
    opened_at: str
    status: TradeStatus = "open"
    closed_at: str = ""
    close_price: float = 0.0
    pnl_usdt: float = 0.0


class TradeStore:
    """
    Manages open and closed trade state using SQLite (open trades) and CSV (closed trades).
    """

    def __init__(self, db_path: str = DB_PATH, csv_path: str = CLOSED_CSV_PATH):
        self.db_path = db_path
        self.csv_path = csv_path
        self._init_db()
        self._init_csv()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

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
                    opened_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    closed_at TEXT DEFAULT '',
                    close_price REAL DEFAULT 0.0,
                    pnl_usdt REAL DEFAULT 0.0
                )
            """)
            conn.commit()
        logger.debug("SQLite open trades DB initialized at %s", self.db_path)

    def _init_csv(self) -> None:
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=Trade.__dataclass_fields__.keys())
                writer.writeheader()
            logger.debug("Closed trades CSV initialized at %s", self.csv_path)

    def save_open_trade(self, trade: Trade) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO open_trades
                (id, symbol, direction, entry_price, stop_loss, take_profit,
                 quantity, risk_amount_usdt, reward_amount_usdt, opened_at,
                 status, closed_at, close_price, pnl_usdt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.id, trade.symbol, trade.direction,
                    trade.entry_price, trade.stop_loss, trade.take_profit,
                    trade.quantity, trade.risk_amount_usdt, trade.reward_amount_usdt,
                    trade.opened_at, trade.status, trade.closed_at,
                    trade.close_price, trade.pnl_usdt,
                ),
            )
            conn.commit()
        logger.debug("Open trade saved: %s %s %s", trade.id, trade.symbol, trade.direction)

    def load_open_trades(self) -> list[Trade]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM open_trades WHERE status = 'open'"
            ).fetchall()

        trades: list[Trade] = []
        for row in rows:
            trades.append(Trade(
                id=row[0], symbol=row[1], direction=row[2],
                entry_price=row[3], stop_loss=row[4], take_profit=row[5],
                quantity=row[6], risk_amount_usdt=row[7], reward_amount_usdt=row[8],
                opened_at=row[9], status=row[10], closed_at=row[11],
                close_price=row[12], pnl_usdt=row[13],
            ))
        return trades

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
            "Trade closed: %s %s %s | PnL: %.2f USDT | Status: %s",
            trade.id, trade.symbol, trade.direction, trade.pnl_usdt, trade.status,
        )

    def _append_closed_csv(self, trade: Trade) -> None:
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=Trade.__dataclass_fields__.keys())
            writer.writerow(asdict(trade))

    def get_all_open_trades(self) -> list[Trade]:
        return self.load_open_trades()
