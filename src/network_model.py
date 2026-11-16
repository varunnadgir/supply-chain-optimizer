"""The network model from notebook 2, packaged as reusable functions.

Notebook 2 builds this model step by step so it can be read and explained.
This module holds the same logic in one place so that other code can call it
without copying cells around:

  - notebook 4 (trade-offs and robustness)
  - src/build_scenarios.py (data for the interactive page)
  - tests/ (automated checks)

tests/test_reproduces_notebooks.py checks that this module gives exactly the
numbers notebook 2 saved to results/.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pulp

from helpers import connect, haversine_km, run_query

ZONE_EDGES = [50, 100, 250, 500, 1000, 1500, 2000]   # km, upper edge of zones 0 to 6
ZONE_LABELS = ["0-50", "50-100", "100-250", "250-500", "500-1,000", "1,000-1,500", "1,500-2,000", "2,000+"]

HANDLING_FEE = 2.00        # R$ per item through a fulfillment center (assumption)
N_TOP_CITIES = 50          # candidate sites: the 50 cities with the most orders ...
                           # ... plus the biggest city in every state


def zone_of(dist_km):
    """Distance in km -> zone number from 0 (local) to 7 (2,000+ km)."""
    return np.digitize(dist_km, ZONE_EDGES, right=True)


def fit_rate_card(ship: pd.DataFrame) -> pd.DataFrame:
    """Per distance zone, fit freight = base + rate_per_kg * weight by least squares."""
    rows = []
    for zone, group in ship.groupby("zone"):
        rate, base = np.polyfit(group["weight_kg"], group["freight_value"], 1)
        transit = group.loc[group["transit_days"] > 0, "transit_days"]
        rows.append({
            "zone": zone,
            "distance_km": ZONE_LABELS[zone],
            "items": len(group),
            "avg_dist_km": group["dist_km"].mean(),
            "avg_freight": group["freight_value"].mean(),
            "base": base,
            "rate_per_kg": rate,
            "median_transit_days": transit.median(),
        })
    return pd.DataFrame(rows).set_index("zone")


@dataclass
class NetworkData:
    """Everything the optimization needs, computed once from the shipments table."""

    ship: pd.DataFrame            # one row per item
    rate_card: pd.DataFrame
    cities: pd.DataFrame          # demand points
    candidates: pd.DataFrame      # candidate sites
    base: np.ndarray              # base[zone]
    rate: np.ndarray              # rate[zone], R$ per kg
    transit: np.ndarray           # transit[zone], median days
    linehaul_rate: float          # R$ per kg per km
    zone_city_site: np.ndarray    # zone of each (city, site) pair
    seller_to_site_km: np.ndarray # distance from each item's seller to each site
    # yearly totals per city, filled in by set_weights()
    items_per_city: np.ndarray = field(default=None)
    kg_per_city: np.ndarray = field(default=None)
    direct_cost: np.ndarray = field(default=None)
    direct_transit: np.ndarray = field(default=None)
    outbound_cost: np.ndarray = field(default=None)
    kg_km_inbound: np.ndarray = field(default=None)

    @property
    def n_cities(self) -> int:
        return len(self.cities)

    @property
    def n_sites(self) -> int:
        return len(self.candidates)

    def set_weights(self, weights=None) -> None:
        """Add up the per-city totals. `weights` says how many times each item counts.

        With no weights every item counts once (the real data). The robustness
        check in notebook 4 passes resampled weights to create "what if demand
        had come out differently" versions of the year.
        """
        ship = self.ship
        w = np.ones(len(ship)) if weights is None else np.asarray(weights, dtype=float)
        city = ship["city_i"].to_numpy()
        kg = ship["weight_kg"].to_numpy()
        n = self.n_cities

        self.items_per_city = np.bincount(city, weights=w, minlength=n)
        self.kg_per_city = np.bincount(city, weights=w * kg, minlength=n)
        self.direct_cost = np.bincount(city, weights=w * ship["predicted_freight"].to_numpy(), minlength=n)
        self.direct_transit = np.bincount(city, weights=w * self.transit[ship["zone"].to_numpy()], minlength=n)
        self.outbound_cost = (
            self.items_per_city[:, None] * self.base[self.zone_city_site]
            + self.kg_per_city[:, None] * self.rate[self.zone_city_site]
        )
        self.kg_km_inbound = np.column_stack([
            np.bincount(city, weights=w * kg * self.seller_to_site_km[:, j], minlength=n)
            for j in range(self.n_sites)
        ])

    # ------------------------------------------------------------ costs

    def fc_cost(self, handling_fee=HANDLING_FEE, linehaul_mult=1.0) -> np.ndarray:
        """Yearly cost of serving each city (row) through each site (column)."""
        return (
            self.outbound_cost
            + handling_fee * self.items_per_city[:, None]
            + linehaul_mult * self.linehaul_rate * self.kg_km_inbound
        )

    def fc_transit(self) -> np.ndarray:
        """Item-days in transit if each city (row) is served from each site (column)."""
        return self.items_per_city[:, None] * self.transit[self.zone_city_site]

    @property
    def baseline_cost(self) -> float:
        return float(self.direct_cost.sum())

    @property
    def baseline_transit(self) -> float:
        return float(self.direct_transit.sum() / self.items_per_city.sum())


def prepare(con=None) -> NetworkData:
    """Load shipments from the database and build every input of the model."""
    own_connection = con is None
    con = con or connect()
    ship = run_query("shipments", con, show_sql=False)
    if own_connection:
        con.close()

    ship["dist_km"] = haversine_km(ship["seller_lat"], ship["seller_lng"], ship["cust_lat"], ship["cust_lng"])
    ship["zone"] = zone_of(ship["dist_km"])
    rate_card = fit_rate_card(ship)
    base = rate_card["base"].to_numpy()
    rate = rate_card["rate_per_kg"].to_numpy()
    transit = rate_card["median_transit_days"].to_numpy()
    ship["predicted_freight"] = base[ship["zone"]] + rate[ship["zone"]] * ship["weight_kg"]
    linehaul_rate, _ = np.polyfit(rate_card["avg_dist_km"], rate_card["rate_per_kg"], 1)

    # demand points: one per customer city
    ship["city_key"] = ship["customer_city"] + ", " + ship["customer_state"]
    cities = (
        ship.groupby("city_key")
        .agg(state=("customer_state", "first"), lat=("cust_lat", "mean"), lng=("cust_lng", "mean"),
             items=("order_id", "size"), orders=("order_id", "nunique"), weight_kg=("weight_kg", "sum"))
        .reset_index()
    )
    ship["city_i"] = ship["city_key"].map({key: i for i, key in enumerate(cities["city_key"])})

    # candidate sites
    by_orders = cities.sort_values("orders", ascending=False)
    candidates = (
        pd.concat([by_orders.head(N_TOP_CITIES), by_orders.groupby("state").head(1)])
        .drop_duplicates("city_key")
        .reset_index(drop=True)
    )

    dist_city_site = haversine_km(
        cities["lat"].to_numpy()[:, None], cities["lng"].to_numpy()[:, None],
        candidates["lat"].to_numpy()[None, :], candidates["lng"].to_numpy()[None, :],
    )
    seller_to_site_km = haversine_km(
        ship["seller_lat"].to_numpy()[:, None], ship["seller_lng"].to_numpy()[:, None],
        candidates["lat"].to_numpy()[None, :], candidates["lng"].to_numpy()[None, :],
    )

    data = NetworkData(
        ship=ship, rate_card=rate_card, cities=cities, candidates=candidates,
        base=base, rate=rate, transit=transit, linehaul_rate=float(linehaul_rate),
        zone_city_site=zone_of(dist_city_site), seller_to_site_km=seller_to_site_km,
    )
    data.set_weights()
    return data


# ---------------------------------------------------------------- the model


def solve_network(n_open: int, fc_score: np.ndarray, direct_score: np.ndarray) -> list[int]:
    """Choose the n_open sites that minimize the total score. Returns the open site numbers.

    `fc_score[i, j]` is what it "costs" to serve city i through site j and
    `direct_score[i]` what it costs to keep shipping direct. In notebook 2 the
    score is freight in R$. Notebook 4 also uses freight plus a R$ value on
    transit time. The model is the same either way.
    """
    n_cities, n_sites = fc_score.shape
    # An FC route that scores worse than shipping direct can never be chosen: leave it out.
    useful = [(i, j) for i in range(n_cities) for j in range(n_sites) if fc_score[i, j] < direct_score[i]]
    sites_for_city: dict[int, list[int]] = {}
    for i, j in useful:
        sites_for_city.setdefault(i, []).append(j)

    model = pulp.LpProblem("fulfillment_network", pulp.LpMinimize)
    y = pulp.LpVariable.dicts("open", range(n_sites), cat="Binary")
    x = pulp.LpVariable.dicts("via_fc", useful, lowBound=0)
    d = pulp.LpVariable.dicts("direct", range(n_cities), lowBound=0)

    model += (
        pulp.lpSum(fc_score[i, j] * x[i, j] for i, j in useful)
        + pulp.lpSum(direct_score[i] * d[i] for i in range(n_cities))
    )
    for i in range(n_cities):
        model += pulp.lpSum(x[i, j] for j in sites_for_city.get(i, [])) + d[i] == 1
    for i, j in useful:
        model += x[i, j] <= y[j]
    model += pulp.lpSum(y.values()) == n_open

    model.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[model.status] != "Optimal":
        raise RuntimeError(f"solver status: {pulp.LpStatus[model.status]}")
    return [j for j in range(n_sites) if y[j].value() > 0.5]


def assign_cities(open_sites, fc_score: np.ndarray, direct_score: np.ndarray) -> np.ndarray:
    """For each city: the open site with the best score, or -1 to keep shipping direct."""
    open_sites = list(open_sites)
    if not open_sites:
        return np.full(len(direct_score), -1)
    via_open = fc_score[:, open_sites]
    best = via_open.argmin(axis=1)
    use_fc = via_open.min(axis=1) < direct_score
    return np.where(use_fc, np.array(open_sites)[best], -1)


def evaluate(data: NetworkData, assigned_site: np.ndarray, fc_cost: np.ndarray) -> dict:
    """Freight cost and service of a network, given which site serves each city."""
    rows = np.arange(data.n_cities)
    use_fc = assigned_site >= 0
    cost = np.where(use_fc, fc_cost[rows, assigned_site], data.direct_cost).sum()
    transit = np.where(use_fc, data.fc_transit()[rows, assigned_site], data.direct_transit).sum()
    total_items = data.items_per_city.sum()
    return {
        "cost": float(cost),
        "saving": float(data.baseline_cost - cost),
        "saving_pct": float(100 * (1 - cost / data.baseline_cost)),
        "transit_days": float(transit / total_items),
        "pct_items_via_fc": float(100 * data.items_per_city[use_fc].sum() / total_items),
    }


def best_network(data: NetworkData, n_open: int, handling_fee=HANDLING_FEE, linehaul_mult=1.0,
                 value_of_a_day=0.0) -> dict:
    """Solve for the best n_open sites and report the result.

    `value_of_a_day` is a R$ value placed on one item arriving one day sooner.
    At 0 the model minimizes freight only (notebook 2).
    """
    fc_cost = data.fc_cost(handling_fee, linehaul_mult)
    fc_score = fc_cost + value_of_a_day * data.fc_transit()
    direct_score = data.direct_cost + value_of_a_day * data.direct_transit
    sites = solve_network(n_open, fc_score, direct_score) if n_open > 0 else []
    assigned = assign_cities(sites, fc_score, direct_score)
    result = evaluate(data, assigned, fc_cost)
    result["sites"] = sorted(sites)
    result["site_names"] = [data.candidates.at[j, "city_key"] for j in sorted(sites)]
    result["assigned_site"] = assigned
    return result


# ---------------------------------------------------------------- many solves in parallel
# Notebook 4 solves the model dozens of times. These helpers spread the solves
# over all CPU cores. Each worker process builds its own copy of the data once.

_WORKER_DATA = None


def _init_worker():
    global _WORKER_DATA
    _WORKER_DATA = prepare()


def _solve_job(job: dict) -> dict:
    data = _WORKER_DATA
    weights = job.pop("weights", None)
    if weights is not None:
        data.set_weights(weights)
    result = best_network(data, **job)
    if weights is not None:
        data.set_weights()          # back to the real data for the next job
    result.pop("assigned_site")
    return result


def solve_many(jobs: list[dict], processes=None) -> list[dict]:
    """Run best_network for each job (a dict of its keyword arguments, plus an
    optional "weights" array for set_weights). Returns results in job order."""
    import os
    from multiprocessing import get_context

    with get_context("fork").Pool(processes or os.cpu_count(), initializer=_init_worker) as pool:
        return pool.map(_solve_job, [dict(j) for j in jobs])
