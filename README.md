# PULSE — Protected Ultra Leverage Strategy Engine

A Python backtesting project for the **LDR (Leveraged Drawdown Reduction)**
strategy, built on [Backtrader](https://www.backtrader.com/). LDR seeks the
upside of a leveraged ETF (TQQQ) while cutting deep drawdowns by rotating out
of TQQQ during severe declines and into defensive assets (UGL, BRK-B).

> ⚠️ **Research only — not investment advice.** This is a historical backtest
> of a mechanical rule set. Past performance does not predict future results.
> Leveraged ETFs carry significant risk, including decay from daily rebalancing.

---

## Strategy in plain English

The portfolio is always fully invested across three assets:

- **TQQQ** — 3x leveraged Nasdaq-100 (the growth engine)
- **UGL** — 2x leveraged gold (defensive / inflation hedge)
- **BRK-B** — Berkshire Hathaway (defensive equity ballast)

In **normal** conditions the portfolio holds **70% TQQQ / 15% UGL / 15% BRK-B**.
It continuously tracks the highest TQQQ closing price seen since it last owned
TQQQ. If TQQQ falls **30% or more** below that tracked peak, LDR exits the TQQQ
sleeve and moves to a **defensive** allocation of **50% UGL / 50% BRK-B**
(the vacated 70% TQQQ sleeve is split evenly onto the two defensive holdings,
each already at 15%, taking them to 50%).

LDR records the TQQQ price at the moment of exit. It re-enters TQQQ only when
TQQQ closes **at or above that stored exit price**, at which point it restores
the 70/15/15 mix and resets the peak tracker to the current price.

Rebalancing back toward target weights is checked **only on quarter-end dates**,
and only executes if some asset has drifted more than **9 percentage points**
from its target. Regime switches (exit / re-enter), however, happen immediately
on any day the trigger fires.

---

## Rule table

| Rule | Detail |
|------|--------|
| Normal weights | TQQQ 70% · UGL 15% · BRK-B 15% |
| Defensive weights | TQQQ 0% · UGL 50% · BRK-B 50% |
| Peak tracking | Max TQQQ close since last entry; normal regime only |
| Exit trigger | TQQQ close ≤ 30% below tracked peak → go defensive |
| Exit reference | Store TQQQ close at exit |
| Re-entry trigger | TQQQ close ≥ stored exit price → go normal, reset peak |
| Rebalance schedule | Quarter-end dates only |
| Rebalance condition | Any weight drifts > 9 pts from target |
| Leverage / margin | None beyond the ETFs; no shorting; no cash target |

All thresholds are configurable in [`config.py`](config.py).

---

## Assets & period

- **Universe:** TQQQ, UGL, BRK-B (yfinance ticker `BRK-B`)
- **Start:** 2010-01-01 requested; effective start is bounded by TQQQ's
  inception (~Feb 2010). Feeds are aligned on the intersection of available
  dates, so the backtest begins once all assets have data.
- **End:** latest available date from Yahoo Finance.
- **Initial capital:** \$100,000

---

## Project structure

The project is layered so features can be added without disturbing the engine:
**UI → services → core**, with a swappable storage layer and a broker seam.

```
pulse/
├── README.md
├── requirements.txt
├── run_backtest.py          # CLI entry (thin wrapper over services)
├── app.py                   # Streamlit home
├── pages/                   # Streamlit multipage UI
│   ├── 1_Backtest.py
│   ├── 2_Portfolio.py
│   ├── 3_Signals.py
│   └── 4_Alerts.py
├── core/                    # backtest engine (no UI/IO concerns)
│   ├── config.py            # all tunable parameters / defaults
│   ├── rules.py             # pure LDR decision logic (shared source of truth)
│   ├── strategy.py          # LDRStrategy + BenchmarkStrategy (Backtrader)
│   ├── data_loader.py       # yfinance download, cleaning, alignment, caching
│   ├── analyzers.py         # performance metrics from the equity curve
│   └── utils.py             # dirs, logging, quarter-end dates, CSV helpers
├── services/                # application logic (UI-agnostic)
│   ├── backtest_service.py  # run backtest -> structured result (+ CSVs)
│   ├── market_data.py       # latest prices / history
│   ├── portfolio_service.py # manual/paper holdings, cash, txns, P&L
│   ├── signal_service.py    # live LDR signals + recommended trades
│   ├── alerts.py            # alert generation + pluggable Notifier
│   └── broker/              # broker seam: base.py (ABC) + paper.py
├── storage/                 # persistence (json_store.py; swappable for a DB)
├── ui/                      # Streamlit helpers (cached wrappers, formatting)
├── data/                    # cached downloads + paper portfolios (auto-created)
├── results/                 # CSV outputs (auto-created)
└── logs/                    # run + alerts logs (auto-created)
```

Both the backtest and the live Signals page evaluate the **same**
`core/rules.py`, so backtested and live behaviour cannot drift apart.

---

## Install

```bash
pip install -r requirements.txt
```

(Recommended: use a virtual environment.)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

**Command line (backtest only):**

```bash
python run_backtest.py
```

Prints a console summary and writes CSVs to `results/`.

**Web UI (backtest + portfolio management):**

```bash
streamlit run app.py
```

Opens a local browser app with four pages:

| Page | What it does |
|------|--------------|
| **Backtest** | Adjust capital, thresholds, and weights in the sidebar, run a backtest, view equity/drawdown charts, and download all CSVs. |
| **Portfolio** | Create paper portfolios; set holdings/cash or record BUY/SELL transactions; see current weights vs. target, drift, and realized/unrealized P&L. |
| **Signals** | Evaluate the live LDR rules against your holdings and the latest close; get recommended buy/sell trades; optionally apply them as paper trades. |
| **Alerts** | Surface stop / re-entry / drift alerts; dispatch them via the notifier (default logs to `logs/alerts.log`). |
| **9-Sig Tracker** | Reads the imported 9-Sig workbook from SQLite and recomputes the quarterly signal (9% line, hold band, 30-Down, spike-reset, throttles) with a per-account trade allocation. Toggle live prices. Tabs for sheet metrics, holdings (hides zero-value rows), and the quarterly log. |
| **Transactions** | Editable ledger (seeded from the sheet) plus statement import: upload a Robinhood/Fidelity/TradeStation statement (CSV or PDF) → extract only strategy-ticker trades (TQQQ/AGG/BRK.B/UGL) + contributions/income → review & edit → commit, with deduplication. Accounts mapped by name + last-4. |

## Architecture & extending

- **`core/`** is the pure engine — no UI or live-data concerns. `core/rules.py`
  holds the LDR decision logic used by *both* the backtest and live signals.
- **`services/`** is application logic callable from any front-end (the CLI, the
  Streamlit UI, or a future API). Add a feature by adding a service.
- **`storage/`** hides persistence behind `load`/`save`. Swap `JsonStore` for a
  database implementation and nothing else changes.
- **`services/broker/`** defines a `BrokerAdapter` ABC. A `PaperBroker` fulfils
  it against the local paper portfolio today; add an `AlpacaBroker` / `IBKRBroker`
  later to read live positions and place real orders — the signal/UI flow already
  speaks this interface.

Planned portfolio-management directions this structure supports: manual/paper
tracking (done), live rebalance signals (done), alerts/monitoring (done), and
broker integration (seam in place, adapter to be added).

## Data storage & privacy

Personal financial data (holdings, account values, imported spreadsheets) is
stored **locally** in a SQLite database at `data/pulse.db`. The entire `data/`
directory is **gitignored** — the database is never committed. Do **not** add
this file to Git (even a private repo); back it up encrypted instead.

- `storage/sqlite_store.py` — SQLite store: lossless raw spreadsheet imports
  (`sheet_imports`), a normalized queryable view (`metrics`), and a generic
  key/value table (`kv`, JsonStore-compatible for future migration).
- `services/sheet_import.py` — imports the "9-Sig TQQQ Tracker" sheet.

**Importing the full workbook (all tabs):** the Google Drive integration can
only export a Sheet's first/summary tab as CSV. To capture every tab, download
the workbook (`File → Download → Microsoft Excel .xlsx`), place it under
`data/imports/`, then:

```python
from services import sheet_import
sheet_import.import_xlsx("data/imports/9sig_tracker.xlsx",
                         source_name="9-Sig TQQQ Tracker")   # needs: pip install openpyxl
```

Each sheet is stored raw and parsed into the `metrics` table.

### Backing up the database to Google Drive

The DB is never in Git, so back it up separately. `scripts/backup_db.py`
supports two methods:

```bash
# 1) rclone — uploads directly to Google Drive (recommended; no Drive Desktop)
brew install rclone            # https://rclone.org/downloads/
rclone config                  # one-time: create a remote named e.g. "gdrive" (type: drive)
python scripts/backup_db.py --rclone gdrive:PULSE_backups
# or: export PULSE_RCLONE_REMOTE="gdrive:PULSE_backups" && python scripts/backup_db.py

# 2) Folder copy — if you use Google Drive for Desktop (auto-detected)
python scripts/backup_db.py                 # or --dest "/path/to/GoogleDrive/PULSE_backups"
```

Each run writes a timestamped `pulse_YYYYMMDD_HHMMSS.db` and prunes to the
newest `--keep N` (default 30). Schedule it with cron for hands-off backups.

For a compact, portable copy, `services/db_backup.py` exports the raw imports +
key/value state to a small JSON snapshot (excluding reproducible price history)
that `restore_snapshot()` rebuilds into a fresh database:

```python
from services import db_backup
db_backup.write_snapshot("data/imports/snapshot.json",
                         exclude_tabs=db_backup.PRICE_HISTORY_TABS)
db_backup.restore_snapshot("data/imports/snapshot.json", db_path="data/pulse.db")
```

> Your original 9-Sig Google Sheet already lives in your Drive, so the source
> records aren't at risk — these backups protect the DB's accumulated state
> (imported snapshots over time and the rolled-forward signal base).

---

## Outputs (in `results/`)

| File | Contents |
|------|----------|
| `daily_equity_curve.csv` | date, portfolio_value, daily_return |
| `trade_log.csv` | date, ticker, action, size, executed_price, value, regime, note |
| `regime_log.csv` | date, regime_before, regime_after, trigger, tqqq_close, tracked_peak, drawdown_from_peak, exit_price_reference |
| `annual_returns.csv` | year, annual_return, running_cagr |
| `summary_metrics.csv` | metric, value (LDR + benchmark) |

Console summary includes start/end dates, initial & final value, CAGR, max
drawdown, Sharpe, annualized volatility, regime switches, and total trades.

---

## Assumptions & caveats

- **Adjusted data:** downloaded with `auto_adjust=True`, so OHLC prices are
  adjusted for splits and dividends. Dividends are thus reflected in price,
  not modeled as separate cash flows.
- **Execution:** `order_target_percent` orders fill at the next bar's open
  (Backtrader default). Weights are decided on the close; fills happen the
  following session.
- **Commissions/slippage:** default 0 (configurable). Real trading costs,
  spreads, and taxes are not modeled.
- **Fully invested:** minor idle cash can arise from share rounding; there is
  no explicit cash target. A small `CASH_BUFFER` (default 0.3%) is applied to
  *buy* legs during a rebalance so a whole-share rounding shortfall on the sell
  legs cannot void the entire buy order (Backtrader nullifies an order whole if
  cash would go negative). This leaves a tiny, transient cash residue well
  inside the drift threshold; the strategy never borrows or leverages.
- **Leveraged ETF decay:** TQQQ/UGL returns already embed daily-rebalance
  volatility decay via their historical prices; no extra modeling is applied.
- **Data source:** Yahoo Finance via yfinance. Data availability and accuracy
  are subject to the provider. Downloads are cached under `data/` (disable with
  `USE_CACHE = False` in `config.py`).
- **Survivorship / point-in-time:** current tickers are used as-is.

---

## Benchmark

`RUN_BENCHMARK = True` (default) also runs a **buy-and-hold** comparison:
70/15/15 with the same quarter-end drift rebalance but **no** stop / regime
switching. Its metrics are appended to `summary_metrics.csv` with a
`benchmark_` prefix and printed in the console summary.

---

---

## A note on the re-entry rule (important)

The specified re-entry rule — *re-enter TQQQ as soon as it closes at or above
the price where the stop fired* — is deliberately simple, but the backtest
shows it **whipsaws badly in choppy bear markets**. During 2022, TQQQ ground
down in a series of 30%+ swings; LDR stopped out and re-entered **8 times**,
each cycle riding TQQQ down ~30% and then buying back at or above the exit
price on the next bounce. The result is that LDR's worst drawdown over the
sample (~−75%, in the 2021-11 → 2023-01 decline) is *comparable to or worse
than* the un-hedged 70/15/15 benchmark, even though LDR spends ~99% of the time
in the normal regime.

This is a faithful implementation of the requested rules, not a bug — it is a
genuine finding: **a stop that re-enters at the exit price offers little
protection in a grinding, oscillating decline.** Practical improvements to
explore would be a re-entry confirmation buffer (e.g. re-enter only above
exit price × (1 + k)), a cooldown period, or a trend filter. All thresholds
live in [`config.py`](config.py) for experimentation.

---

*Strategy name "LDR" is a neutral placeholder. This project is for educational
and research purposes only.*
