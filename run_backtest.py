"""
PULSE — Protected Ultra Leverage Strategy Engine
CLI entry point for the LDR (Leveraged Drawdown Reduction) backtest.

Thin wrapper around services.backtest_service so the CLI and the Streamlit UI
share one code path.

Run with:
    python run_backtest.py
"""

from core import config, utils
from services import backtest_service


def _print_summary(metrics, bench, logger):
    logger.info("=" * 60)
    logger.info("  PULSE / LDR STRATEGY — BACKTEST SUMMARY")
    logger.info("=" * 60)
    line = lambda k, v: logger.info(f"  {k:<26}: {v}")
    line("Start date", metrics["start_date"])
    line("End date", metrics["end_date"])
    line("Initial capital", f"${metrics['initial_capital']:,.2f}")
    line("Final portfolio value", f"${metrics['final_value']:,.2f}")
    line("CAGR", f"{metrics['cagr'] * 100:.2f}%")
    line("Max drawdown", f"{metrics['max_drawdown'] * 100:.2f}%")
    line("Sharpe ratio", f"{metrics['sharpe_ratio']:.3f}")
    line("Annualized volatility", f"{metrics['annualized_volatility'] * 100:.2f}%")
    line("Regime switches", metrics["regime_switches"])
    line("Total trades", metrics["total_trades"])
    line("Time in normal regime", f"{metrics.get('pct_time_normal', 0) * 100:.1f}%")
    line("Time in defensive regime", f"{metrics.get('pct_time_defensive', 0) * 100:.1f}%")
    if bench is not None:
        logger.info("-" * 60)
        logger.info("  BENCHMARK (70/15/15 quarterly, no stop)")
        logger.info("-" * 60)
        line("Benchmark CAGR", f"{bench['cagr'] * 100:.2f}%")
        line("Benchmark max drawdown", f"{bench['max_drawdown'] * 100:.2f}%")
        line("Benchmark Sharpe", f"{bench['sharpe_ratio']:.3f}")
        line("Benchmark final value", f"${bench['final_value']:,.2f}")
    logger.info("=" * 60)


def main():
    utils.ensure_dirs(config.RESULTS_DIR, config.LOGS_DIR)
    logger = utils.setup_logger(config.LOGS_DIR)
    logger.info("Starting PULSE / LDR backtest ...")

    result = backtest_service.run(write_csv=True, logger=logger)
    logger.info(f"Wrote CSV outputs to {config.RESULTS_DIR}/")
    _print_summary(result.metrics, result.benchmark_metrics, logger)
    logger.info("Done.")


if __name__ == "__main__":
    main()
