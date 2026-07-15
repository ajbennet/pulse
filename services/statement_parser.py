"""
Brokerage statement parser.

Turns a Robinhood / Fidelity / TradeStation statement (CSV or PDF) into a list
of normalized transaction dicts:

    {date, ticker, action, shares, price, fees, cash_flow, notes}

The CSV path is format-tolerant (fuzzy column detection) and handles most broker
CSV exports today. The PDF path extracts tables/lines best-effort. Broker- and
layout-specific adapters plug in via BROKER_ADAPTERS once real samples exist —
until then everything routes through the generic path.

Downstream, transactions_service applies the strategy-ticker filter and dedup;
this module only extracts and normalizes.
"""

import csv
import io
import re
from typing import Callable, Dict, List, Optional

# ----------------------------------------------------------------------
# Action normalization
# ----------------------------------------------------------------------
_ACTION_RULES = [
    ("BUY", ("buy", "bought", "purchase", "reinvest shares")),
    ("SELL", ("sell", "sold", "redemption", "redeem")),
    ("DIVIDEND", ("dividend", "reinvest dividend", "div ")),
    ("INTEREST", ("interest",)),
    ("CONTRIBUTION", ("contribution", "deposit", "ach in", "transfer in",
                      "electronic funds transfer received", "funds received")),
    ("WITHDRAWAL", ("withdrawal", "ach out", "transfer out", "debit")),
]


def normalize_action(text: str) -> str:
    s = (text or "").strip().lower()
    for action, keys in _ACTION_RULES:
        if any(k in s for k in keys):
            return action
    return (text or "").strip().upper()


# ----------------------------------------------------------------------
# Column detection (fuzzy header matching)
# ----------------------------------------------------------------------
# Fields are resolved in this order; each header is claimed by at most one field,
# so a "date" column can't later be mistaken for "action".
_COLUMN_ALIASES = [
    ("date", ["trade date", "run date", "settlement date", "activity date",
              "process date", "as of date", "date"]),
    ("shares", ["quantity", "share quantity", "shares", "qty", "units"]),
    ("price", ["average price", "execution price", "share price", "price ($)", "price"]),
    ("fees", ["fees & comm", "commission", "reg fee", "fees", "fee"]),
    ("cash_flow", ["net amount", "cash flow", "amount ($)", "net amount ($)", "amount",
                   "value", "total"]),
    ("ticker", ["symbol", "ticker", "instrument", "security", "description"]),
    ("action", ["trans code", "transaction code", "transaction type", "action",
                "activity type", "transaction", "type", "description"]),
]


def _match_columns(headers: List[str]) -> Dict[str, str]:
    lowered = [(h.lower().strip(), h) for h in headers if h]
    mapping: Dict[str, str] = {}
    used = set()
    for field, aliases in _COLUMN_ALIASES:
        for alias in aliases:
            hit = next((orig for low, orig in lowered
                        if orig not in used and low == alias), None)  # exact
            if not hit:
                hit = next((orig for low, orig in lowered
                            if orig not in used and alias in low), None)  # substring
            if hit:
                mapping[field] = hit
                used.add(hit)
                break
    return mapping


_NUM_RE = re.compile(r"[^0-9.\-]")


def _num(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")   # (123.45) => -123.45
    s = _NUM_RE.sub("", s)
    if s in ("", "-", "."):
        return None
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return None


_TICKER_RE = re.compile(r"\b(TQQQ|AGG|UGL|BRK[.\-/ ]?B)\b", re.IGNORECASE)


def _extract_ticker(raw: str) -> str:
    """Pull a strategy ticker out of a symbol or a free-text description."""
    if not raw:
        return ""
    m = _TICKER_RE.search(str(raw))
    return m.group(1).upper() if m else str(raw).strip()


# ----------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------
def parse_csv(data: bytes) -> Dict:
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    warnings: List[str] = []

    # Find the header line (some broker CSVs have preamble rows).
    reader_rows = list(csv.reader(io.StringIO(text)))
    header_idx = None
    for i, r in enumerate(reader_rows[:25]):
        low = [c.lower() for c in r]
        if any("date" in c for c in low) and any(
                any(a in c for a in ("symbol", "quantity", "action", "amount", "description"))
                for c in low):
            header_idx = i
            break
    if header_idx is None:
        return {"rows": [], "warnings": ["Could not locate a header row in the CSV."]}

    headers = [c.strip() for c in reader_rows[header_idx]]
    colmap = _match_columns(headers)
    if "date" not in colmap:
        warnings.append("No date column detected.")

    rows = []
    for r in reader_rows[header_idx + 1:]:
        if not any(c.strip() for c in r):
            continue
        rec = {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
        def g(field):
            col = colmap.get(field)
            return rec.get(col, "") if col else ""
        symbol_raw = g("ticker")
        action = normalize_action(g("action"))
        row = {
            "date": g("date").strip(),
            "ticker": _extract_ticker(symbol_raw),
            "action": action,
            "shares": _num(g("shares")),
            "price": _num(g("price")),
            "fees": _num(g("fees")),
            "cash_flow": _num(g("cash_flow")),
            "notes": (g("action") or "").strip()[:120],
        }
        if not row["date"] and row["shares"] is None and row["cash_flow"] is None:
            continue
        rows.append(row)

    return {"rows": rows, "warnings": warnings, "columns": colmap}


# ----------------------------------------------------------------------
# PDF (best-effort; requires pdfplumber)
# ----------------------------------------------------------------------
def parse_pdf(data: bytes) -> Dict:
    try:
        import pdfplumber
    except ImportError:
        return {"rows": [], "warnings": ["PDF parsing needs pdfplumber (pip install pdfplumber)."]}

    warnings: List[str] = []
    rows: List[Dict] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if not table or len(table) < 2:
                    continue
                headers = [(c or "").strip() for c in table[0]]
                colmap = _match_columns(headers)
                if "date" not in colmap and "ticker" not in colmap:
                    continue
                for r in table[1:]:
                    rec = {headers[i]: (r[i] or "") for i in range(min(len(headers), len(r)))}
                    def g(field):
                        col = colmap.get(field)
                        return rec.get(col, "") if col else ""
                    row = {
                        "date": str(g("date")).strip(),
                        "ticker": _extract_ticker(g("ticker")),
                        "action": normalize_action(g("action")),
                        "shares": _num(g("shares")),
                        "price": _num(g("price")),
                        "fees": _num(g("fees")),
                        "cash_flow": _num(g("cash_flow")),
                        "notes": str(g("action")).strip()[:120],
                    }
                    if row["date"] or row["shares"] is not None:
                        rows.append(row)
    if not rows:
        warnings.append("No transaction tables detected in the PDF — a broker-specific "
                        "PDF adapter is likely needed (share a sample).")
    return {"rows": rows, "warnings": warnings}


# ----------------------------------------------------------------------
# Detection + broker adapters (extensible)
# ----------------------------------------------------------------------
def detect_broker(filename: str, sample_text: str = "") -> str:
    f = (filename or "").lower()
    t = (sample_text or "").lower()
    for broker in ("robinhood", "fidelity", "tradestation"):
        if broker in f or broker in t:
            return broker
    return "unknown"


# Broker-specific adapters get registered here once real samples exist:
#   BROKER_ADAPTERS["robinhood"] = lambda data, fmt: {...}
BROKER_ADAPTERS: Dict[str, Callable] = {}


def last4_from_text(text: str) -> Optional[str]:
    """Best-effort: pull an account number's last 4 digits from statement text."""
    m = re.search(r"(?:account|acct)[^0-9]{0,12}(?:[xX*\-]+)?(\d{4})\b", text or "")
    if m:
        return m.group(1)
    m = re.search(r"[xX*]{2,}[\-\s]?(\d{4})\b", text or "")
    return m.group(1) if m else None


def parse_file(filename: str, data: bytes) -> Dict:
    """
    Parse a statement file into normalized rows.

    Returns {broker, format, rows, warnings, last4?}. Applies a broker adapter
    if registered; otherwise the generic CSV/PDF path.
    """
    is_pdf = filename.lower().endswith(".pdf") or (isinstance(data, bytes) and data[:5] == b"%PDF-")
    fmt = "pdf" if is_pdf else "csv"
    sample = "" if is_pdf else (data[:4000].decode("utf-8-sig", errors="replace")
                                if isinstance(data, bytes) else data[:4000])
    broker = detect_broker(filename, sample)

    if broker in BROKER_ADAPTERS:
        result = BROKER_ADAPTERS[broker](data, fmt)
    else:
        result = parse_pdf(data) if is_pdf else parse_csv(data)

    result.update({"broker": broker, "format": fmt})
    result.setdefault("last4", last4_from_text(sample))
    return result
