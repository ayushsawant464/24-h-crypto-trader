import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from bot.config.settings import settings
from bot.logs.logger import logger

DB_PATH = settings.LOGS_DIR / "state.db"

@dataclass
class PersistedPosition:
    pair: str
    side: str
    entry_price: float
    peak_price: float
    quantity: float
    effective_stop_price: float
    is_ratcheted: bool
    is_trailing: bool
    atr_15m: float
    updated_at: float

class StateStore:
    """
    Local SQLite State Persistence Store.
    Survives process restarts to prevent:
    - State amnesia (losing positions or resetting entry prices)
    - Stop-loss drift (re-registering losing positions at depressed prices)
    - Circuit breaker cooldown timer loss
    - Immediate post-stop repurchasing (enforces 12-hour quarantine ledger)
    - Concurrency locks (uses WAL journal mode + thread-safe RLock)
    """
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=15000;")
        return conn

    def _init_db(self):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Active positions table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS positions (
                        pair TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        peak_price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        effective_stop_price REAL NOT NULL,
                        is_ratcheted INTEGER NOT NULL,
                        is_trailing INTEGER NOT NULL,
                        atr_15m REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                # Circuit breaker state table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS circuit_breaker (
                        id INTEGER PRIMARY KEY,
                        high_watermark REAL NOT NULL,
                        is_active INTEGER NOT NULL,
                        cooldown_until REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                # Asset quarantine table (post-stop repurchase prevention)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS asset_quarantine (
                        pair TEXT PRIMARY KEY,
                        quarantine_until REAL NOT NULL,
                        reason TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                """)
                conn.commit()

    # --- Position Persistence ---
    def save_position(self, pos: PersistedPosition):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO positions (
                        pair, side, entry_price, peak_price, quantity,
                        effective_stop_price, is_ratcheted, is_trailing, atr_15m, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pos.pair, pos.side, pos.entry_price, pos.peak_price, pos.quantity,
                    pos.effective_stop_price, 1 if pos.is_ratcheted else 0,
                    1 if pos.is_trailing else 0, pos.atr_15m, time.time()
                ))
                conn.commit()

    def load_positions(self) -> Dict[str, PersistedPosition]:
        positions = {}
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM positions")
                for row in cursor.fetchall():
                    positions[row["pair"]] = PersistedPosition(
                        pair=row["pair"],
                        side=row["side"],
                        entry_price=row["entry_price"],
                        peak_price=row["peak_price"],
                        quantity=row["quantity"],
                        effective_stop_price=row["effective_stop_price"],
                        is_ratcheted=bool(row["is_ratcheted"]),
                        is_trailing=bool(row["is_trailing"]),
                        atr_15m=row["atr_15m"],
                        updated_at=row["updated_at"]
                    )
        return positions

    def remove_position(self, pair: str):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM positions WHERE pair = ?", (pair,))
                conn.commit()

    def clear_all_positions(self):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM positions")
                conn.commit()

    # --- Circuit Breaker State Persistence ---
    def save_circuit_breaker(self, high_watermark: float, is_active: bool, cooldown_until: float):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO circuit_breaker (id, high_watermark, is_active, cooldown_until, updated_at)
                    VALUES (1, ?, ?, ?, ?)
                """, (high_watermark, 1 if is_active else 0, cooldown_until, time.time()))
                conn.commit()

    def load_circuit_breaker(self) -> Tuple[float, bool, float]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT high_watermark, is_active, cooldown_until FROM circuit_breaker WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    return float(row["high_watermark"]), bool(row["is_active"]), float(row["cooldown_until"])
        return 0.0, False, 0.0

    # --- Asset Quarantine Ledger ---
    def quarantine_asset(self, pair: str, duration_hours: float = 12.0, reason: str = "Stop Loss Hit"):
        quarantine_until = time.time() + (duration_hours * 3600.0)
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO asset_quarantine (pair, quarantine_until, reason, created_at)
                    VALUES (?, ?, ?, ?)
                """, (pair, quarantine_until, reason, time.time()))
                conn.commit()
        logger.info(f"[QUARANTINE LEDGER] {pair} quarantined for {duration_hours:.1f}h until {time.ctime(quarantine_until)} ({reason})")

    def is_quarantined(self, pair: str) -> bool:
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT quarantine_until FROM asset_quarantine WHERE pair = ?", (pair,))
                row = cursor.fetchone()
                if row:
                    if now < float(row["quarantine_until"]):
                        return True
                    else:
                        # Clean up expired quarantine
                        cursor.execute("DELETE FROM asset_quarantine WHERE pair = ?", (pair,))
                        conn.commit()
        return False

    def get_active_quarantines(self) -> Dict[str, float]:
        now = time.time()
        active = {}
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT pair, quarantine_until FROM asset_quarantine WHERE quarantine_until > ?", (now,))
                for row in cursor.fetchall():
                    active[row["pair"]] = float(row["quarantine_until"])
        return active
