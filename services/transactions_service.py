"""
Transactions service — the writable ledger behind the 9-Sig tracker.

Seeds from the imported sheet Transactions, then accepts new entries from
statement parsers or manual edits. Only strategy tickers are kept
(TQQQ / AGG / BRK.B / UGL) plus cash events (contributions/income); everything
else is ignored. Duplicates are collapsed via a stable dedup key.
"""

import hashlib
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from storage.sqlite_store import SqliteStore

# Tickers this strategy trades. Everything else in a statement is ignored.
STRATEGY_TICKERS = {"TQQQ", "AGG", "BRK.B", "UGL"}
# Cash / income actions kept even without a strategy ticker.
CASH_ACTIONS = {"CONTRIBUTION", "WITHDRAWAL", "DIVIDEND", "INTEREST"}

_BRKB_ALIASES = {"BRK-B", "BRKB", "BRK.B", "BRK B", "BRK/B"}


def _s(v) -> str:
    """Coerce any value (incl. NaN/None/float) to a clean string."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def normalize_ticker(t) -> str:
    s = _s(t).upper()
    if not s:
        return ""
    # strip exchange prefixes like NASDAQ:TQQQ / NYSE:BRK.B
    if ":" in s:
        s = s.split(":", 1)[1]
    if s in _BRKB_ALIASES:
        return "BRK.B"
    return s


def is_strategy_row(ticker: str, action: str) -> bool:
    """Keep strategy-ticker trades and cash/income events; drop other stocks."""
    tk = normalize_ticker(ticker)
    act = _s(action).upper()
    if tk in STRATEGY_TICKERS:
        return True
    if act in CASH_ACTIONS and tk in ("", "CASH"):
        return True
    return False


def _norm_date(d) -> str:
    s = _s(d)
    if not s:
        return ""
    try:
        return pd.to_datetime(s).date().isoformat()
    except Exception:
        return s


def dedup_key(row: Dict) -> str:
    """
    Stable identity for a transaction: account + date + ticker + action +
    rounded shares/price. Two statements of the same fill collapse to one.
    """
    parts = [
        (row.get("account_last4") or row.get("account") or "").strip().lower(),
        _norm_date(row.get("date")),
        normalize_ticker(row.get("ticker")),
        (row.get("action") or "").strip().upper(),
        f"{float(row.get('shares') or 0):.4f}",
        f"{float(row.get('price') or 0):.4f}",
        f"{float(row.get('cash_flow') or 0):.2f}",
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def _clean_row(row: Dict, source: str) -> Dict:
    out = dict(row)
    out["ticker"] = normalize_ticker(row.get("ticker"))
    out["date"] = _norm_date(row.get("date"))
    out["action"] = _s(row.get("action")).upper()
    out["notes"] = _s(row.get("notes"))
    for f in ("shares", "price", "fees", "cash_flow", "contribution_amount", "trade_value"):
        v = row.get(f)
        if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
            out[f] = None
        else:
            try:
                out[f] = float(v)
            except (TypeError, ValueError):
                out[f] = None
    out["source"] = source
    out["created_at"] = datetime.now().isoformat(timespec="seconds")
    out["dedup_key"] = dedup_key(out)
    if "include_9sig" not in out:
        out["include_9sig"] = 1
    return out


def add_transaction(row: Dict, source: str = "manual", store: Optional[SqliteStore] = None) -> bool:
    store = store or SqliteStore()
    return store.insert_transaction(_clean_row(row, source))


def import_rows(rows: List[Dict], source: str, store: Optional[SqliteStore] = None,
                strategy_only: bool = True) -> Dict:
    """Bulk-insert parsed rows. Returns counts of added / skipped-dup / filtered."""
    store = store or SqliteStore()
    added = skipped = filtered = 0
    for r in rows:
        if strategy_only and not is_strategy_row(r.get("ticker"), r.get("action")):
            filtered += 1
            continue
        if store.insert_transaction(_clean_row(r, source)):
            added += 1
        else:
            skipped += 1
    return {"added": added, "duplicates": skipped, "filtered_out": filtered}


def transactions_df(store: Optional[SqliteStore] = None) -> pd.DataFrame:
    store = store or SqliteStore()
    rows = [dict(r) for r in store.list_transactions()]
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Seeding from the imported sheet
# ----------------------------------------------------------------------
_SHEET_COLMAP = {
    "Date": "date", "Account ID": "account", "Ticker": "ticker", "Action": "action",
    "Shares": "shares", "Price": "price", "Fees": "fees", "Cash Flow": "cash_flow",
    "Contribution Amount": "contribution_amount", "Trade Value": "trade_value",
    "Quarter": "quarter", "Notes": "notes",
}


def seed_from_sheet(store: Optional[SqliteStore] = None) -> Dict:
    """
    One-time seed of the transactions ledger from the imported sheet
    Transactions tab. Idempotent (dedup), and maps A1..A10 -> account name.
    """
    store = store or SqliteStore()
    df = store.load_table("Transactions")
    if df.empty:
        return {"added": 0, "duplicates": 0, "filtered_out": 0}

    # Map legacy account IDs -> names using the Accounts tab.
    acc = store.load_table("Accounts")
    id2name = {}
    if not acc.empty and "Account ID" in acc.columns and "Account Name" in acc.columns:
        id2name = dict(zip(acc["Account ID"].astype(str), acc["Account Name"].astype(str)))

    rows = []
    for _, r in df.iterrows():
        row = {dst: r.get(src) for src, dst in _SHEET_COLMAP.items() if src in df.columns}
        # Skip the sheet's blank template rows (nothing meaningful filled in).
        if not any(_s(row.get(k)) for k in ("date", "action", "ticker", "shares",
                                            "cash_flow", "contribution_amount")):
            continue
        aid = "" if pd.isna(row.get("account")) else str(row.get("account") or "")
        row["account"] = id2name.get(aid, aid)
        note = row.get("notes")
        note = "" if note is None or (isinstance(note, float) and pd.isna(note)) else str(note)
        row["notes"] = (note + (f" [{aid}]" if aid else "")).strip()
        rows.append(row)

    # Keep contributions and strategy trades; sheet has no other-stocks anyway.
    return import_rows(rows, source="sheet", store=store, strategy_only=True)


def seed_accounts_from_sheet(store: Optional[SqliteStore] = None) -> int:
    """Seed the accounts table (name + legacy A-ID) from the Accounts tab."""
    store = store or SqliteStore()
    acc = store.load_table("Accounts")
    n = 0
    if acc.empty:
        return 0
    for _, r in acc.iterrows():
        name = str(r.get("Account Name") or "").strip()
        if not name:
            continue
        store.upsert_account(name=name, legacy_id=str(r.get("Account ID") or "").strip() or None)
        n += 1
    return n
