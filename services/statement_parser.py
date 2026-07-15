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
# Order matters — more specific phrases first.
_ACTION_RULES = [
    ("DIVIDEND", ("dividend received", "dividend", "div ")),
    ("REINVEST", ("reinvestment", "reinvest")),
    ("BUY", ("you bought", "bought", "buy", "purchase")),
    ("SELL", ("you sold", "sold", "sell", "redemption", "redeem")),
    ("INTEREST", ("interest",)),
    ("CONTRIBUTION", ("contribution", "deposit", "ach in", "transfer in",
                      "electronic funds transfer received", "funds received")),
    ("WITHDRAWAL", ("withdrawal", "normal distr", "distribution", "ach out",
                    "transfer out", "debit")),
    ("EXCHANGE", ("exchange in", "exchange out", "exchange")),
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
    ("account_number", ["account number", "account #", "acct number", "acct #", "account no"]),
    ("account", ["account name", "account"]),
    ("shares", ["quantity", "share quantity", "shares", "qty", "units"]),
    ("price", ["average price", "execution price", "share price", "price ($)", "price"]),
    ("fees", ["fees & comm", "reg fee", "fees ($)", "fees", "fee", "commission"]),
    ("cash_flow", ["net amount", "cash flow", "amount ($)", "net amount ($)", "amount",
                   "value", "total"]),
    ("ticker", ["symbol", "ticker", "instrument", "security", "description"]),
    ("action", ["trans code", "transaction code", "transaction type", "action",
                "activity type", "transaction", "type", "description"]),
]


def _last4(s: str) -> Optional[str]:
    digits = re.sub(r"\D", "", str(s or ""))
    return digits[-4:] if len(digits) >= 4 else None


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
        shares = _num(g("shares"))
        row = {
            "date": g("date").strip(),
            "ticker": _extract_ticker(symbol_raw),
            "action": action,
            "shares": abs(shares) if shares is not None else None,
            "price": _num(g("price")),
            "fees": _num(g("fees")),
            "cash_flow": _num(g("cash_flow")),
            "account_hint": g("account").strip(),
            "account_last4": _last4(g("account_number")),
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


# Map broker security names -> strategy tickers (order matters; check specific first).
_SECURITY_TO_TICKER = [
    ("ultrapro qqq", "TQQQ"),          # ProShares UltraPro QQQ (3x)
    ("aggregate bond", "AGG"),         # iShares Core US Aggregate Bond ETF
    ("berkshire hathaway", "BRK.B"),
    ("ultra gold", "UGL"),             # ProShares Ultra Gold
]


def map_security(name: str) -> str:
    low = (name or "").lower()
    for key, ticker in _SECURITY_TO_TICKER:
        if key in low:
            return ticker
    return (name or "").strip()


def _map_rh_account(text: str) -> str:
    t = (text or "").lower()
    if "roth" in t:
        return "Robinhood Roth IRA"
    if "traditional" in t:
        return "Robinhood Traditional IRA"
    if "custodial" in t or "utma" in t:
        return "Robinhood UTMA"
    if "individual" in t or "regular" in t:
        return "Robinhood"
    return "Robinhood"


import datetime as _dt

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _rh_date(mon: str, day: str, asof) -> str:
    """Robinhood shows 'Jul 4' (no year). Infer year from the as-of date."""
    ay, am, ad = asof
    m = _MONTHS.get(mon, 1)
    d = int(day)
    year = ay if (m, d) <= (am, ad) else ay - 1
    try:
        return _dt.date(year, m, d).isoformat()
    except ValueError:
        return f"{year}-{m:02d}-{d:02d}"


_RH_TRADE = re.compile(r"^(.*?) (limit|market) (buy|sell) \$([\d,]+\.\d{2})$")
_RH_DETAIL = re.compile(r"^(.*?) · ([A-Z][a-z]{2}) (\d{1,2}) ([\d.]+) shares at \$([\d.]+)")


def parse_robinhood(data: bytes, fmt: str) -> Dict:
    """
    Robinhood adapter. CSV exports go through the generic parser; the History-page
    PDF is line-block text, parsed here for BUY/SELL trades (mapped to tickers,
    with per-row account from the account/date line). Cash/dividend rows and
    canceled orders are skipped for now.
    """
    if fmt != "pdf":
        return parse_csv(data)
    try:
        import pdfplumber
    except ImportError:
        return {"rows": [], "warnings": ["PDF parsing needs pdfplumber."]}

    lines: List[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").split("\n"))

    asof = (2000 + _dt.date.today().year % 100, 12, 31)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2})\b", lines[0] if lines else "")
    if m:
        asof = (2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))

    rows, warnings = [], []
    pending = None
    for ln in lines:
        ln = ln.strip()
        mt = _RH_TRADE.match(ln)
        if mt:
            pending = {"security": mt.group(1).strip(), "side": mt.group(3),
                       "amount": float(mt.group(4).replace(",", ""))}
            continue
        if pending:
            md = _RH_DETAIL.match(ln)
            if md:
                side = pending["side"]
                rows.append({
                    "date": _rh_date(md.group(2), md.group(3), asof),
                    "ticker": map_security(pending["security"]),
                    "action": side.upper(),
                    "shares": float(md.group(4)),
                    "price": float(md.group(5)),
                    "fees": 0.0,
                    "cash_flow": (pending["amount"] if side == "sell" else -pending["amount"]),
                    "account_hint": _map_rh_account(md.group(1)),
                    "notes": f"robinhood {pending['security']}",
                })
                pending = None
    warnings.append("Robinhood PDF: extracted trades only (dividends/cash/contributions "
                    "and canceled orders are not imported).")
    return {"rows": rows, "warnings": warnings}


# Broker adapters registry. Signature: adapter(data: bytes, fmt: str) -> {rows, warnings}.
BROKER_ADAPTERS: Dict[str, Callable] = {
    "robinhood": parse_robinhood,
}


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
