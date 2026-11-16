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
