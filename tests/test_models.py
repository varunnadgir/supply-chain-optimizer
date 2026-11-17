"""Automated checks for the project. Run from the repository root:

    pip install pytest
    pytest -q

They need data/olist.db (see data/README.md) and take about a minute.

What they check:
  1. The rate card's predicted freight adds up to the real total freight.
  2. The optimizer agrees with brute force (trying every option) for 1 and 2 sites.
  3. The reusable model in src/ reproduces the numbers notebooks 2 and 3 saved.
  4. Basic sanity: more centers never cost more, and the "ship direct" option
     means a network can never be worse than today.
"""

import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import inventory_model as im  # noqa: E402
import network_model as nm  # noqa: E402

RESULTS = ROOT / "results"


@pytest.fixture(scope="module")
def data():
    if not (ROOT / "data" / "olist.db").exists():
        pytest.skip("data/olist.db not built; see data/README.md")
    return nm.prepare()


def test_rate_card_reproduces_total_freight(data):
    actual = data.ship["freight_value"].sum()
    predicted = data.ship["predicted_freight"].sum()
    assert predicted == pytest.approx(actual, rel=1e-9)


def test_rate_card_matches_saved_results(data):
    saved = pd.read_csv(RESULTS / "rate_card.csv", index_col="zone")
    for column in ("base", "rate_per_kg", "median_transit_days"):
        np.testing.assert_allclose(data.rate_card[column], saved[column], rtol=1e-9)


@pytest.mark.parametrize("n_open", [1, 2])
def test_solver_matches_brute_force(data, n_open):
    fc_cost = data.fc_cost()

    def total(sites):
        return np.minimum(fc_cost[:, list(sites)].min(axis=1), data.direct_cost).sum()

    brute = min(combinations(range(data.n_sites), n_open), key=total)
    solver = nm.solve_network(n_open, fc_cost, data.direct_cost)
    assert total(solver) == pytest.approx(total(brute), rel=1e-9)


def test_reproduces_notebook_2(data):
    saved = pd.read_csv(RESULTS / "network_by_number_of_fcs.csv", index_col="fcs")
    result = nm.best_network(data, 6)
    assert result["cost"] == pytest.approx(saved.loc[6, "total_cost"], rel=1e-9)
    assert result["transit_days"] == pytest.approx(saved.loc[6, "transit_days"], rel=1e-9)
    assert sorted(saved.loc[6, "sites"].split("; ")) == sorted(result["site_names"])


def test_reproduces_notebook_3():
    if not (ROOT / "data" / "olist.db").exists():
        pytest.skip("data/olist.db not built")
    assignments = pd.read_csv(RESULTS / "city_assignments.csv")
    served_by = {c: s for c, s in zip(assignments["city_key"], assignments["served_by"]) if s != "direct from seller"}
    inv = im.size_inventory(im.load_weekly_demand(), served_by)
    policy = pd.read_csv(RESULTS / "inventory_policy.csv")
    assert inv["safety_units"] == pytest.approx(policy["safety_stock"].sum())


def test_more_centers_never_cost_more():
    saved = pd.read_csv(RESULTS / "network_by_number_of_fcs.csv", index_col="fcs")
    assert saved["total_cost"].is_monotonic_decreasing


def test_network_never_worse_than_today(data):
    for fee in (0, 6, 50):
        result = nm.best_network(data, 1, handling_fee=fee)
        assert result["saving"] >= -1e-6


# ---------------------------------------------------------------- notebooks 5 to 7

import simulation as sim  # noqa: E402
import stochastic_model as sm  # noqa: E402


@pytest.fixture(scope="module")
def regions(data):
    return sm.aggregate(data)[0]


def test_regions_keep_every_item(data, regions):
    assert regions.items.sum() == pytest.approx(data.items_per_city.sum())
    assert regions.direct_cost.sum() == pytest.approx(data.baseline_cost)


def test_grouping_barely_changes_cost(data, regions):
    six = nm.best_network(data, 6)
    grouped = np.minimum(regions.fc_cost()[:, six["sites"]].min(axis=1), regions.direct_cost).sum()
    assert abs(grouped / six["cost"] - 1) < 0.01


def test_stochastic_design_never_worse_than_direct(regions):
    futures = sm.make_scenarios(sorted(set(regions.state)), 3, seed=0)
    plan = sm.design(regions, futures)
    assert plan["status"] == "Optimal"
    for s in range(len(futures)):
        result = sm.operate(regions, plan["sites"], futures.iloc[s])
        assert result["cost"] <= result["baseline_cost"] + 1e-6


def test_capacity_is_respected(regions):
    future = pd.Series({s: 2.0 for s in set(regions.state)})
    built = [(0, "small")]                       # one small building at the first candidate site
    share = sm.route_shares(regions, built, future)
    load = (np.array([future[s] for s in regions.state]) * regions.items * share[:, 0]).sum()
    assert load <= sm.SIZES.at["small", "capacity"] + 1e-6


def test_simulation_routes_every_item_and_replays_today(data):
    items, daily = sim.run(data, sim.Settings())
    assert items["route"].ne("").all()
    direct = items["route"].str.startswith("direct")
    # items that ship direct keep exactly their real purchase-to-door time
    pd.testing.assert_series_equal(items.loc[direct, "sim_door_days"], items.loc[direct, "door_days"],
                                   check_names=False)
    summary = sim.summarize(items)
    assert 85 < summary["fill_rate_pct"] < 99
    assert (daily["backlog"] >= 0).all()
