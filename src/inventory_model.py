"""The inventory sizing from notebook 3, packaged so it can run for any network.

Notebook 3 sizes stock for the 6-center network step by step. Notebook 4 needs
the same calculation for networks of 1 to 8 centers, so this module holds it as
functions. tests/ checks that it reproduces notebook 3 for the 6-center case.
"""

import math

import numpy as np
import pandas as pd
from scipy.stats import norm

from helpers import connect, run_query

ALPHA = 0.3            # exponential smoothing weight (chosen in notebook 3)
REVIEW_WEEKS = 1
LEAD_WEEKS = 1         # assumption
SERVICE_LEVEL = 0.95
HOLDING_RATE = 0.25    # assumption: yearly holding cost as a share of stock value
N_CATEGORIES = 10      # stock the 10 best-selling categories

HISTORY_START = "2017-09-04"
TRAIN_START, TRAIN_END = "2018-01-01", "2018-04-30"   # train weeks: [start, end)


def load_weekly_demand(con=None) -> pd.DataFrame:
    """Units per week, per category, per customer city (the weekly_category_demand query)."""
    own = con is None
    con = con or connect()
    weekly = run_query("weekly_category_demand", con, show_sql=False)
    if own:
        con.close()
    weekly["week_start"] = pd.to_datetime(weekly["week_start"])
    weekly["city_key"] = weekly["customer_city"] + ", " + weekly["customer_state"]
    return weekly


def smoothing_forecast(y: np.ndarray, alpha: float = ALPHA) -> np.ndarray:
    forecast = [y[0]]
    for t in range(1, len(y)):
        forecast.append(alpha * y[t - 1] + (1 - alpha) * forecast[-1])
    return np.array(forecast)


def size_inventory(weekly: pd.DataFrame, served_by: dict) -> dict:
    """Safety stock, cycle stock and yearly holding cost for one network.

    `served_by` maps city_key -> name of the center serving it. Cities missing
    from the map, or mapped to None, ship direct and need no stock.
    """
    w = weekly.assign(center=weekly["city_key"].map(served_by)).dropna(subset=["center"])
    if w.empty:
        return {"safety_units": 0.0, "cycle_units": 0.0, "inventory_value": 0.0, "holding_cost": 0.0,
                "holding_cost_all_categories": 0.0, "stocked_share": 1.0}

    recent = w[w["week_start"] >= TRAIN_START]
    by_cat = recent.groupby("category")["units"].sum().sort_values(ascending=False)
    stocked = list(by_cat.head(N_CATEGORIES).index)
    stocked_share = by_cat.head(N_CATEGORIES).sum() / by_cat.sum()

    demand = (
        w[w["category"].isin(stocked)]
        .pivot_table(index="week_start", columns=["center", "category"], values="units", aggfunc="sum", fill_value=0)
        .asfreq("W-MON", fill_value=0)
        .astype(float)
    )
    history = demand[demand.index >= HISTORY_START]
    train = (history.index >= TRAIN_START) & (history.index < TRAIN_END)

    forecast = pd.DataFrame({c: smoothing_forecast(history[c].to_numpy()) for c in history.columns},
                            index=history.index)
    sigma = (history - forecast)[train].std()
    z = norm.ppf(SERVICE_LEVEL)
    safety = np.ceil(z * sigma * math.sqrt(REVIEW_WEEKS + LEAD_WEEKS))
    avg_weekly = history[train].mean()
    cycle = avg_weekly * REVIEW_WEEKS / 2

    price = (
        recent[recent["category"].isin(stocked)].groupby("category")
        .apply(lambda g: g["revenue"].sum() / g["units"].sum(), include_groups=False)
    )
    unit_price = pd.Series([price[cat] for _, cat in history.columns], index=history.columns)
    value = float(((safety + cycle) * unit_price).sum())
    holding = HOLDING_RATE * value
    return {
        "safety_units": float(safety.sum()),
        "cycle_units": float(cycle.sum()),
        "inventory_value": value,
        "holding_cost": holding,
        "holding_cost_all_categories": float(holding / stocked_share),
        "stocked_share": float(stocked_share),
    }
