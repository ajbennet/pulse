"""
Utility helpers for the LDR strategy project: directory setup, logging,
CSV writing, and small quarter-end date helpers.
"""

import logging
import os

import pandas as pd


def ensure_dirs(*dirs):
    """Create directories if they do not already exist."""
    for d in dirs:
        if d:
            os.makedirs(d, exist_ok=True)


def setup_logger(logs_dir, name="ldr"):
    """Configure a logger that writes to both console and a log file."""
    ensure_dirs(logs_dir)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(os.path.join(logs_dir, "run.log"), mode="w")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def quarter_end_dates(dates):
    """
    Given an ordered iterable of datetime.date/Timestamp objects, return the
    set of dates that are the last available trading day of each calendar
    quarter (Mar, Jun, Sep, Dec).
    """
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates))).sort_values()
    if len(idx) == 0:
        return set()
    # Group by (year, quarter) and take the max (last) date in each group.
    df = pd.DataFrame({"date": idx})
    df["yq"] = df["date"].dt.year.astype(str) + "Q" + df["date"].dt.quarter.astype(str)
    last_dates = df.groupby("yq")["date"].max()
    return {d.date() for d in last_dates}


def save_csv(df, path):
    """Write a DataFrame to CSV and return the path."""
    df.to_csv(path, index=False)
    return path
