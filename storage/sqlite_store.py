"""
SQLite persistence for PULSE.

Holds imported spreadsheet data losslessly plus a queryable, normalized view,
and a generic key/value table (same load/save shape as JsonStore, so services
can migrate onto SQLite later without interface changes).

The database file lives under data/ and is gitignored — it holds personal
financial data and must never be committed.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, List, Optional, Sequence, Tuple

DEFAULT_DB = os.path.join("data", "pulse.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sheet_imports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT,           -- e.g. Google Drive file id
    source_name  TEXT,           -- workbook name
    tab          TEXT,           -- tab / sheet name
    captured_at  TEXT,           -- snapshot time reported by the sheet (if any)
    imported_at  TEXT NOT NULL,  -- wall-clock import time (ISO)
    raw_csv      TEXT NOT NULL   -- lossless original
);

CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id   INTEGER NOT NULL REFERENCES sheet_imports(id) ON DELETE CASCADE,
    section     TEXT,
    key         TEXT NOT NULL,
    value_text  TEXT,
    value_num   REAL,
    captured_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_key ON metrics(key);
CREATE INDEX IF NOT EXISTS idx_metrics_import ON metrics(import_id);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT
);

-- Long-format storage for tabular tabs (Transactions, Holdings, prices, ...).
-- Lossless and schema-agnostic: any table can be reconstructed to a DataFrame.
CREATE TABLE IF NOT EXISTS sheet_cells (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id  INTEGER NOT NULL REFERENCES sheet_imports(id) ON DELETE CASCADE,
    tab        TEXT NOT NULL,
    row_idx    INTEGER NOT NULL,
    col_name   TEXT NOT NULL,
    value_text TEXT,
    value_num  REAL
);
CREATE INDEX IF NOT EXISTS idx_cells_import ON sheet_cells(import_id);
CREATE INDEX IF NOT EXISTS idx_cells_tab ON sheet_cells(tab);
"""


class SqliteStore:
    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Spreadsheet imports
    # ------------------------------------------------------------------
    def record_import(self, raw_csv: str, imported_at: str, source_id: str = "",
                      source_name: str = "", tab: str = "", captured_at: str = "") -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO sheet_imports (source_id, source_name, tab, captured_at, "
                "imported_at, raw_csv) VALUES (?,?,?,?,?,?)",
                (source_id, source_name, tab, captured_at, imported_at, raw_csv),
            )
            return cur.lastrowid

    def add_metrics(self, import_id: int, rows: Sequence[Tuple], captured_at: str = "") -> int:
        """rows: iterable of (section, key, value_text, value_num)."""
        with self._conn() as c:
            c.executemany(
                "INSERT INTO metrics (import_id, section, key, value_text, value_num, "
                "captured_at) VALUES (?,?,?,?,?,?)",
                [(import_id, s, k, vt, vn, captured_at) for (s, k, vt, vn) in rows],
            )
        return len(rows)

    def latest_import(self, tab: Optional[str] = None) -> Optional[sqlite3.Row]:
        q = "SELECT * FROM sheet_imports"
        args: List[Any] = []
        if tab:
            q += " WHERE tab = ?"
            args.append(tab)
        q += " ORDER BY id DESC LIMIT 1"
        with self._conn() as c:
            return c.execute(q, args).fetchone()

    def metrics_for(self, import_id: int) -> List[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT section, key, value_text, value_num FROM metrics "
                "WHERE import_id = ? ORDER BY id", (import_id,)
            ).fetchall()

    def latest_metrics(self, tab: Optional[str] = None) -> List[sqlite3.Row]:
        imp = self.latest_import(tab)
        return self.metrics_for(imp["id"]) if imp else []

    # ------------------------------------------------------------------
    # Tabular tabs
    # ------------------------------------------------------------------
    def add_cells(self, import_id: int, tab: str, cells: Sequence[Tuple]) -> int:
        """cells: iterable of (row_idx, col_name, value_text, value_num)."""
        with self._conn() as c:
            c.executemany(
                "INSERT INTO sheet_cells (import_id, tab, row_idx, col_name, "
                "value_text, value_num) VALUES (?,?,?,?,?,?)",
                [(import_id, tab, ri, cn, vt, vn) for (ri, cn, vt, vn) in cells],
            )
        return len(cells)

    def load_table(self, tab: str, import_id: Optional[int] = None):
        """
        Reconstruct a tabular tab as a pandas DataFrame from its latest (or a
        specific) import. Numeric columns use value_num where available.
        """
        import pandas as pd
        if import_id is None:
            imp = self.latest_import(tab)
            if not imp:
                return pd.DataFrame()
            import_id = imp["id"]
        with self._conn() as c:
            rows = c.execute(
                "SELECT row_idx, col_name, value_text, value_num FROM sheet_cells "
                "WHERE import_id = ? AND tab = ? ORDER BY row_idx", (import_id, tab)
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        data = {}
        for r in rows:
            val = r["value_text"] if r["value_num"] is None else r["value_num"]
            data.setdefault(r["row_idx"], {})[r["col_name"]] = val
        return pd.DataFrame.from_dict(data, orient="index").reset_index(drop=True)

    def all_imports(self) -> List[sqlite3.Row]:
        """Every recorded import with its raw CSV (for compact backups)."""
        with self._conn() as c:
            return c.execute(
                "SELECT source_id, source_name, tab, captured_at, imported_at, raw_csv "
                "FROM sheet_imports ORDER BY id"
            ).fetchall()

    def all_kv(self) -> List[sqlite3.Row]:
        with self._conn() as c:
            return c.execute("SELECT key, value_json, updated_at FROM kv").fetchall()

    # ------------------------------------------------------------------
    # Generic key/value (JsonStore-compatible)
    # ------------------------------------------------------------------
    def load(self, key: str, default: Any = None) -> Any:
        with self._conn() as c:
            row = c.execute("SELECT value_json FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def save(self, key: str, value: Any, updated_at: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO kv (key, value_json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(value, default=str), updated_at),
            )

    def exists(self, key: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM kv WHERE key = ?", (key,)).fetchone() is not None

    def delete(self, key: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM kv WHERE key = ?", (key,))
