"""
Compact export / restore of the PULSE database.

Dumps the losslessly-stored raw spreadsheet imports plus the key/value state
(e.g. the rolled-forward 9-Sig signal base) to a small JSON snapshot — a few
tens of KB rather than the multi-MB SQLite file — that can be uploaded to
Google Drive and later restored into a fresh database.

Restore re-parses each raw CSV through the same importer, so the reconstructed
`metrics` and `sheet_cells` match the original.
"""

import json
from datetime import datetime
from typing import Optional

from services import sheet_import
from storage.sqlite_store import DEFAULT_DB, SqliteStore

SNAPSHOT_VERSION = 1

# Large, reproducible price-history tabs — excluded from compact/cloud snapshots.
PRICE_HISTORY_TABS = {"Daily_Prices", "Raw_TQQQ", "Raw_AGG", "Raw_BRKB"}


def export_snapshot(store: Optional[SqliteStore] = None, exclude_tabs=None) -> dict:
    store = store or SqliteStore()
    exclude = set(exclude_tabs or [])
    imports = [dict(r) for r in store.all_imports() if r["tab"] not in exclude]
    kv = [dict(r) for r in store.all_kv()]
    return {
        "version": SNAPSHOT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "excluded_tabs": sorted(exclude),
        "imports": imports,
        "kv": kv,
    }


def write_snapshot(path: str, store: Optional[SqliteStore] = None, exclude_tabs=None) -> dict:
    snap = export_snapshot(store, exclude_tabs=exclude_tabs)
    with open(path, "w") as fh:
        json.dump(snap, fh)
    import os
    return {"path": path, "bytes": os.path.getsize(path),
            "imports": len(snap["imports"]), "kv": len(snap["kv"])}


def restore_snapshot(snapshot, db_path: str = DEFAULT_DB) -> dict:
    """Rebuild a database from a snapshot dict or JSON file path."""
    if isinstance(snapshot, str):
        with open(snapshot, "r") as fh:
            snapshot = json.load(fh)

    store = SqliteStore(db_path)
    for imp in snapshot.get("imports", []):
        sheet_import.import_raw(
            imp["raw_csv"], tab=imp.get("tab", ""),
            source_id=imp.get("source_id", ""), source_name=imp.get("source_name", ""),
            captured_at=imp.get("captured_at", ""), imported_at=imp.get("imported_at", ""),
            store=store,
        )
    for row in snapshot.get("kv", []):
        store.save(row["key"], json.loads(row["value_json"]), updated_at=row.get("updated_at", ""))

    return {"restored_imports": len(snapshot.get("imports", [])),
            "restored_kv": len(snapshot.get("kv", [])), "db_path": db_path}
