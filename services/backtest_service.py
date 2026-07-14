"""
Backtest service — the single entry point for running an LDR backtest.

Both the CLI (`run_backtest.py`) and the Streamlit UI call `run()` so there is
one code path. Returns a structured `BacktestResult` (metrics + DataFrames) and
can optionally persist the CSV outputs.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional

import backtrader as bt
import pandas as pd

from core import analyzers, config, data_loader, utils
from core.strategy import BenchmarkStrategy, LDRStrategy


@dataclass
class RunConfig:
    """Runtime-overridable backtest settings (defaults come from core.config)."""
    tickers: list = field(default_factory=lambda: list(config.TICKERS))
    start_date: str = config.START_DATE
    end_date: Optional[str] = config.END_DATE
    initial_capital: float = config.INITIAL_CAPITAL
    commission: float = config.COMMISSION
    stop_drawdown: float = config.STOP_DRAWDOWN_THRESHOLD
    rebalance_drift: float = config.REBALANCE_DRIFT_THRESHOLD
    cash_buffer: float = config.CASH_BUFFER
    normal_weights: Dict[str, float] = field(default_factory=lambda: dict(config.NORMAL_WEIGHTS))
    defensive_weights: Dict[str, float] = field(default_factory=lambda: dict(config.DEFENSIVE_WEIGHTS))
    run_benchmark: bool = config.RUN_BENCHMARK


@dataclass
class BacktestResult:
    metrics: Dict
    equity: pd.DataFrame                       # date, portfolio_value, daily_return
    trades: pd.DataFrame
    regimes: pd.DataFrame
    annual: pd.DataFrame
    benchmark_metrics: Optional[Dict] = None
    benchmark_equity: Optional[pd.DataFrame] = None


TRADE_COLS = ["date", "ticker", "action", "size", "executed_price", "value", "regime", "note"]
REGIME_COLS = ["date", "regime_before", "regime_after", "trigger", "tqqq_close",
               "tracked_peak", "drawdown_from_peak", "exit_price_reference"]


def _add_feeds(cerebro, data, tickers):
    for ticker in tickers:
        df = data[ticker]
        cerebro.adddata(bt.feeds.PandasData(
            dataname=df, name=ticker,
            open="open", high="high", low="low", close="close",
            volume="volume", openinterest=None,
        ))


def _run_strategy(strategy_cls, data, rc, qe_dates, **strat_kwargs):
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(rc.initial_capital)
    cerebro.broker.setcommission(commission=rc.commission)
    # See run_backtest / README: cheat-on-close + sells-before-buys + cash buffer.
    cerebro.broker.set_coc(True)
    _add_feeds(cerebro, data, rc.tickers)
    cerebro.addstrategy(strategy_cls, quarter_end_dates=qe_dates, **strat_kwargs)
    return cerebro.run()[0]


def _equity_df(equity_curve):
    equity = analyzers.equity_series(equity_curve)
    df = pd.DataFrame({
        "date": equity.index.date,
        "portfolio_value": equity.values.round(2),
    })
    df["daily_return"] = equity.pct_change().fillna(0.0).round(8).values
    return df


def run(rc: Optional[RunConfig] = None, data=None, write_csv: bool = False,
        logger=None) -> BacktestResult:
    """Run the LDR backtest (and optional benchmark) and return structured results."""
    rc = rc or RunConfig()

    if data is None:
        data = data_loader.load_data(tickers=rc.tickers, start=rc.start_date,
                                     end=rc.end_date, logger=logger)
    common_dates = data[rc.tickers[0]].index
    qe_dates = frozenset(utils.quarter_end_dates(common_dates))

    strat = _run_strategy(
        LDRStrategy, data, rc, qe_dates,
        normal_weights=rc.normal_weights, defensive_weights=rc.defensive_weights,
        stop_drawdown=rc.stop_drawdown, rebalance_drift=rc.rebalance_drift,
        cash_buffer=rc.cash_buffer,
    )
    if logger and getattr(strat, "rejected_orders", 0):
        logger.warning(f"{strat.rejected_orders} order(s) were rejected/margin-blocked.")

    metrics = analyzers.compute_metrics(
        strat.equity_curve, regime_records=strat.regime_records,
        trades_count=strat.trades_count, regime_switches=strat.regime_switches,
        initial_capital=rc.initial_capital, label="LDR",
    )

    equity = _equity_df(strat.equity_curve)
    trades = pd.DataFrame(strat.trade_records, columns=TRADE_COLS)
    regimes = pd.DataFrame(strat.regime_records, columns=REGIME_COLS)
    annual = analyzers.annual_returns(analyzers.equity_series(strat.equity_curve))

    bench_metrics = None
    bench_equity = None
    if rc.run_benchmark:
        bench = _run_strategy(BenchmarkStrategy, data, rc, qe_dates,
                              weights=rc.normal_weights, rebalance_drift=rc.rebalance_drift,
                              cash_buffer=rc.cash_buffer)
        bench_metrics = analyzers.compute_metrics(
            bench.equity_curve, regime_records=None, trades_count=0,
            regime_switches=0, initial_capital=rc.initial_capital, label="benchmark",
        )
        bench_equity = _equity_df(bench.equity_curve)

    result = BacktestResult(
        metrics=metrics, equity=equity, trades=trades, regimes=regimes,
        annual=annual, benchmark_metrics=bench_metrics, benchmark_equity=bench_equity,
    )
    if write_csv:
        write_outputs(result)
    return result


def write_outputs(result: BacktestResult, results_dir=None):
    """Persist the five CSV outputs to the results directory."""
    rdir = results_dir or config.RESULTS_DIR
    utils.ensure_dirs(rdir)
    result.equity.to_csv(os.path.join(rdir, "daily_equity_curve.csv"), index=False)
    result.trades.to_csv(os.path.join(rdir, "trade_log.csv"), index=False)
    result.regimes.to_csv(os.path.join(rdir, "regime_log.csv"), index=False)
    result.annual.to_csv(os.path.join(rdir, "annual_returns.csv"), index=False)

    rows = [{"metric": k, "value": v} for k, v in result.metrics.items()]
    if result.benchmark_metrics is not None:
        rows += [{"metric": f"benchmark_{k}", "value": v}
                 for k, v in result.benchmark_metrics.items()]
    pd.DataFrame(rows, columns=["metric", "value"]).to_csv(
        os.path.join(rdir, "summary_metrics.csv"), index=False)
