"""SQLite storage for price history and run tracking."""

import sqlite3
import json
import datetime
from pathlib import Path
from typing import Optional
from dataclasses import asdict

from .config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get a connection to the SQLite database, creating tables if needed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            site TEXT NOT NULL,
            products_found INTEGER DEFAULT 0,
            error TEXT,
            snapshot_path TEXT
        );

        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id),
            collected_at TEXT NOT NULL,
            site TEXT NOT NULL,
            product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            brand TEXT DEFAULT '',
            weight_g REAL,
            price_rub REAL NOT NULL,
            price_per_100g REAL,
            price_per_kg REAL,
            in_stock INTEGER DEFAULT 1,
            url TEXT DEFAULT '',
            raw_json TEXT DEFAULT '{}',
            match_tier TEXT DEFAULT 'wide',
            product_key TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_prices_site ON prices(site);
        CREATE INDEX IF NOT EXISTS idx_prices_product_key ON prices(product_key);
        CREATE INDEX IF NOT EXISTS idx_prices_collected ON prices(collected_at);
    """)


def start_run(site: str) -> int:
    """Record the start of a scraping run. Returns run_id."""
    conn = get_connection()
    now = datetime.datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO runs (started_at, status, site) VALUES (?, 'running', ?)",
        (now, site),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(run_id: int, products_found: int = 0, error: str = None,
               snapshot_path: str = None) -> None:
    """Mark a run as finished."""
    conn = get_connection()
    now = datetime.datetime.now().isoformat()
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, products_found=?, error=?, snapshot_path=? WHERE id=?",
        (now, 'ok' if not error else 'failed', products_found, error, snapshot_path, run_id),
    )
    conn.commit()


def save_prices(run_id: int, products: list[dict], now: str = None) -> int:
    """Save normalized product prices. Returns count of saved rows."""
    if now is None:
        now = datetime.datetime.now().isoformat()
    conn = get_connection()
    count = 0
    for p in products:
        conn.execute(
            """INSERT INTO prices
               (run_id, collected_at, site, product_id, name, brand,
                weight_g, price_rub, price_per_100g, price_per_kg,
                in_stock, url, raw_json, match_tier, product_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                now,
                p.get("site", ""),
                p.get("product_id", ""),
                p.get("name", ""),
                p.get("brand", ""),
                p.get("weight_g"),
                p.get("price_rub", 0),
                p.get("price_per_100g"),
                p.get("price_per_kg"),
                p.get("in_stock", 1),
                p.get("url", ""),
                json.dumps(p.get("raw", {}), ensure_ascii=False),
                p.get("match_tier", "wide"),
                p.get("product_key", ""),
            ),
        )
        count += 1
    conn.commit()
    return count


def get_last_run_prices(site: str = None) -> list[dict]:
    """Get prices from the most recent successful run(s)."""
    conn = get_connection()
    if site:
        query = """
            SELECT p.* FROM prices p
            JOIN runs r ON p.run_id = r.id
            WHERE r.status = 'ok' AND p.site = ?
            AND r.id = (SELECT MAX(id) FROM runs WHERE status = 'ok' AND site = ?)
        """
        rows = conn.execute(query, (site, site)).fetchall()
    else:
        query = """
            SELECT p.* FROM prices p
            WHERE p.run_id IN (
                SELECT MAX(id) FROM runs WHERE status = 'ok' GROUP BY site
            )
        """
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def get_price_trends(product_keys: list[str], limit: int = 5) -> dict[str, list[dict]]:
    """Get recent price history for specific product keys."""
    conn = get_connection()
    trends = {}
    for key in product_keys:
        rows = conn.execute(
            """SELECT p.* FROM prices p
               WHERE p.product_key = ?
               ORDER BY p.collected_at DESC LIMIT ?""",
            (key, limit),
        ).fetchall()
        trends[key] = [dict(r) for r in rows]
    return trends


def get_previous_run_date(site: str) -> Optional[str]:
    """Get the date of the previous successful run for a site."""
    conn = get_connection()
    row = conn.execute(
        "SELECT started_at FROM runs WHERE status='ok' AND site=? ORDER BY id DESC LIMIT 1",
        (site,),
    ).fetchone()
    return row["started_at"] if row else None
