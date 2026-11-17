"""Network design for an uncertain future (notebook 5).

Notebook 2 chose sites for last year's demand. A real network is built once and
then used for years while demand grows, unevenly by region. This module chooses
sites AND building sizes so that the network does well on average across many
possible futures. That is a two-stage stochastic program:

  stage 1 (decide now, once):     which sites to open, and how big to build each
  stage 2 (decide in each future): how to route each region's orders, given the
                                   buildings that exist

Three things are added to the model of notebook 2:

  - a yearly fixed cost per building, so the model chooses how many to open
  - a capacity per building (two sizes to pick from), with overflow shipped direct
  - optionally, a delivery promise: a minimum share of items delivered in two days

To keep the model small enough to solve many futures at once, small towns are
grouped with the nearest large city in their state (see `aggregate`).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pulp

from helpers import haversine_km
from network_model import HANDLING_FEE, NetworkData

# Emission factors in kg CO2e per tonne-km of product moved (assumptions, see notebook 7)
PARCEL_KG_CO2_PER_TKM = 0.25   # parcel network: vans and part-loaded trucks
TRUCK_KG_CO2_PER_TKM = 0.10    # full truck, seller to center (GLEC: about 0.07-0.11 for articulated trucks)

TWO_DAY_ZONES = 1      # zones 0 and 1 (up to 100 km) have median transit of 2.2 days or less
N_ANCHORS = 300        # the largest cities keep their own demand point

# Building sizes (assumptions, see notebook 5): yearly fixed cost in R$, capacity in items a year
SIZES = pd.DataFrame(
    {"fixed_cost": [15_000.0, 25_000.0], "capacity": [20_000.0, 50_000.0]},
    index=["small", "large"],
)


@dataclass
class Regions:
    """Demand grouped into regions. Every array is per region (rows) and per site (columns)."""
    names: list            # the anchor city of each region
    state: np.ndarray      # state of each region
    items: np.ndarray
    fc_var_cost: np.ndarray       # freight through each site, without the handling fee
    direct_cost: np.ndarray
    fc_two_day: np.ndarray        # items that would arrive within two days via each site
    direct_two_day: np.ndarray
    fc_item_days: np.ndarray
    direct_item_days: np.ndarray
    fc_parcel_tkm: np.ndarray     # tonne-km by parcel network, center to customer
    fc_bulk_tkm: np.ndarray       # tonne-km by full truck, seller to center
    direct_parcel_tkm: np.ndarray # tonne-km by parcel network, seller to customer
    site_names: list

    def fc_cost(self, handling_fee=HANDLING_FEE):
        return self.fc_var_cost + handling_fee * self.items[:, None]

    def fc_co2(self, parcel=PARCEL_KG_CO2_PER_TKM, truck=TRUCK_KG_CO2_PER_TKM):
        """kg of CO2 a year if each region (row) is served through each site (column)."""
        return parcel * self.fc_parcel_tkm + truck * self.fc_bulk_tkm

    def direct_co2(self, parcel=PARCEL_KG_CO2_PER_TKM):
        """kg of CO2 a year if each region keeps shipping direct."""
        return parcel * self.direct_parcel_tkm


def aggregate(data: NetworkData, n_anchors=N_ANCHORS) -> tuple[Regions, np.ndarray]:
    """Group the 3,811 customer cities into regions.

    Anchors are the n_anchors cities with the most items, every candidate site,
    and the largest city of every state. Every other city joins the nearest
    anchor in its own state. Returns the regions and, for each city, its region.
    """
    cities = data.cities
    by_items = cities.sort_values("items", ascending=False)
    anchor_keys = (
        set(by_items.head(n_anchors)["city_key"])
        | set(data.candidates["city_key"])
        | set(by_items.groupby("state").head(1)["city_key"])
    )
    is_anchor = cities["city_key"].isin(anchor_keys).to_numpy()
    anchor_idx = np.flatnonzero(is_anchor)

    region_of_city = np.empty(len(cities), dtype=int)
    region_of_city[anchor_idx] = np.arange(len(anchor_idx))
    lat, lng, state = cities["lat"].to_numpy(), cities["lng"].to_numpy(), cities["state"].to_numpy()
    for i in np.flatnonzero(~is_anchor):
        same_state = anchor_idx[state[anchor_idx] == state[i]]
        dist = haversine_km(lat[i], lng[i], lat[same_state], lng[same_state])
        region_of_city[i] = np.flatnonzero(anchor_idx == same_state[np.argmin(dist)])[0]

    n = len(anchor_idx)

    def by_region(values):            # add up a per-city array (1-D or 2-D) by region
        values = np.asarray(values)
        out = np.zeros((n,) + values.shape[1:])
        np.add.at(out, region_of_city, values)
        return out

    items = data.items_per_city
    two_day_site = (data.zone_city_site <= TWO_DAY_ZONES) * items[:, None]
    ship = data.ship
    direct_two_day_city = np.bincount(ship["city_i"], weights=(ship["zone"] <= TWO_DAY_ZONES).astype(float),
                                      minlength=data.n_cities)
    fc_var = data.fc_cost(handling_fee=0.0)
    city_site_km = haversine_km(lat[:, None], lng[:, None],
                                data.candidates["lat"].to_numpy()[None, :], data.candidates["lng"].to_numpy()[None, :])
    direct_kg_km = np.bincount(ship["city_i"], weights=ship["weight_kg"] * ship["dist_km"], minlength=data.n_cities)

    regions = Regions(
        names=list(cities["city_key"].to_numpy()[anchor_idx]),
        state=state[anchor_idx],
        items=by_region(items),
        fc_var_cost=by_region(fc_var),
        direct_cost=by_region(data.direct_cost),
        fc_two_day=by_region(two_day_site),
        direct_two_day=by_region(direct_two_day_city),
        fc_item_days=by_region(data.fc_transit()),
        direct_item_days=by_region(data.direct_transit),
        fc_parcel_tkm=by_region(data.kg_per_city[:, None] * city_site_km / 1000),
        fc_bulk_tkm=by_region(data.kg_km_inbound / 1000),
        direct_parcel_tkm=by_region(direct_kg_km / 1000),
        site_names=list(data.candidates["city_key"]),
    )
    return regions, region_of_city


# ---------------------------------------------------------------- futures


def make_scenarios(states, n, seed, growth_median=1.5, growth_sd=0.25, regional_sd=0.20):
    """Random futures: one demand multiplier per state, for n futures.

    multiplier = national growth x regional deviation
      national growth   lognormal, median growth_median (1.5 = 50% more items
                        than in the study year), spread growth_sd (log scale)
      regional deviation lognormal, median 1, spread regional_sd, independent by state
    Returns a DataFrame with one row per future and one column per state.
    """
    rng = np.random.default_rng(seed)
    states = sorted(set(states))
    national = growth_median * np.exp(rng.normal(0, growth_sd, size=(n, 1)))
    regional = np.exp(rng.normal(0, regional_sd, size=(n, len(states))))
    return pd.DataFrame(national * regional, columns=states)


# ---------------------------------------------------------------- the model


def design(regions: Regions, scenarios: pd.DataFrame, handling_fee=HANDLING_FEE, sizes=SIZES,
           two_day_share=None, risk_weight=0.0, tail=0.10, carbon_price=0.0, max_sites=None, time_limit=900,
           gap=0.002, msg=False):
    """Choose sites and building sizes to minimize fixed cost + average freight over the futures.

    two_day_share: if given (for example 0.6), in EVERY future at least that share
        of items must be delivered within two days.
    carbon_price: R$ per tonne of CO2, added to the cost of every route.
    risk_weight: 0 cares only about the average future. Values up to 1 put that
        weight on the worst `tail` share of futures instead (measured by the
        saving against shipping everything direct; this is called CVaR).
    Returns a dict: sites (list of (site number, size name)), objective and solver details.
    """
    C = regions.fc_cost(handling_fee) + carbon_price / 1000 * regions.fc_co2()
    D = regions.direct_cost + carbon_price / 1000 * regions.direct_co2()
    n_reg, n_sites = C.shape
    S = len(scenarios)
    mult = scenarios[list(regions.state)].to_numpy() if False else np.column_stack(
        [scenarios[s].to_numpy() for s in regions.state])          # mult[s, r]

    # Arcs worth having: cheaper than shipping direct, or (with a promise) two-day arcs
    useful = C < D[:, None]
    if two_day_share is not None:
        useful |= regions.fc_two_day > 0.5 * regions.items[:, None]
    arcs = [(r, j) for r in range(n_reg) for j in range(n_sites) if useful[r, j]]
    arcs_of_site = {}
    for r, j in arcs:
        arcs_of_site.setdefault(j, []).append(r)
    arcs_of_region = {}
    for r, j in arcs:
        arcs_of_region.setdefault(r, []).append(j)
    sites = sorted(arcs_of_site)

    m = pulp.LpProblem("stochastic_network", pulp.LpMinimize)
    y = {(j, k): pulp.LpVariable(f"build_{j}_{k}", cat="Binary") for j in sites for k in sizes.index}
    x = {(r, j, s): pulp.LpVariable(f"x_{r}_{j}_{s}", lowBound=0) for r, j in arcs for s in range(S)}
    d = {(r, s): pulp.LpVariable(f"d_{r}_{s}", lowBound=0) for r in range(n_reg) for s in range(S)}

    fixed = pulp.lpSum(sizes.at[k, "fixed_cost"] * y[j, k] for j, k in y)
    # loss[s]: what the network costs in future s, minus what shipping everything direct would cost
    loss = [
        fixed
        + pulp.lpSum(mult[s, r] * C[r, j] * x[r, j, s] for r, j in arcs)
        + pulp.lpSum(mult[s, r] * D[r] * d[r, s] for r in range(n_reg))
        - float((mult[s] * D).sum())
        for s in range(S)
    ]
    expected = (1.0 / S) * pulp.lpSum(loss)
    if risk_weight > 0:
        # CVaR: the average loss over the worst `tail` share of futures, written as a linear program
        eta = pulp.LpVariable("eta")
        excess = [pulp.LpVariable(f"excess_{s}", lowBound=0) for s in range(S)]
        for s in range(S):
            m += excess[s] >= loss[s] - eta
        cvar = eta + (1.0 / (tail * S)) * pulp.lpSum(excess)
        m += (1 - risk_weight) * expected + risk_weight * cvar
    else:
        m += expected

    for j in sites:
        m += pulp.lpSum(y[j, k] for k in sizes.index) <= 1                       # one building per site
    if max_sites is not None:
        m += pulp.lpSum(y.values()) <= max_sites
    for s in range(S):
        for r in range(n_reg):
            m += pulp.lpSum(x[r, j, s] for j in arcs_of_region.get(r, [])) + d[r, s] == 1
        for j in sites:
            built = pulp.lpSum(y[j, k] for k in sizes.index)
            capacity = pulp.lpSum(sizes.at[k, "capacity"] * y[j, k] for k in sizes.index)
            m += pulp.lpSum(mult[s, r] * regions.items[r] * x[r, j, s] for r in arcs_of_site[j]) <= capacity
            for r in arcs_of_site[j]:
                m += x[r, j, s] <= built
        if two_day_share is not None:
            on_time = pulp.lpSum(mult[s, r] * regions.fc_two_day[r, j] * x[r, j, s] for r, j in arcs) \
                + pulp.lpSum(mult[s, r] * regions.direct_two_day[r] * d[r, s] for r in range(n_reg))
            m += on_time >= two_day_share * float((mult[s] * regions.items).sum())

    m.solve(pulp.HiGHS(msg=msg, timeLimit=time_limit, gapRel=gap))
    status = pulp.LpStatus[m.status]
    built = sorted((j, k) for (j, k), v in y.items() if v.value() > 0.5)
    return {"sites": built, "objective": pulp.value(m.objective), "status": status,
            "fixed_cost": float(sum(sizes.at[k, "fixed_cost"] for _, k in built)),
            "n_arcs": len(arcs)}


def route_shares(regions: Regions, built, multipliers: pd.Series, handling_fee=HANDLING_FEE, sizes=SIZES,
                 carbon_price=0.0) -> np.ndarray:
    """Given the buildings, route one future's demand at least cost (overflow ships direct).

    Returns share[r, j], the fraction of region r's items sent through site j.
    The rest of each region, 1 - share[r].sum(), ships direct.
    """
    score = regions.fc_cost(handling_fee) + carbon_price / 1000 * regions.fc_co2()
    direct_score = regions.direct_cost + carbon_price / 1000 * regions.direct_co2()
    mult = np.array([multipliers[s] for s in regions.state])
    items = mult * regions.items
    cap = {j: sizes.at[k, "capacity"] for j, k in built}
    arcs = [(r, j) for r in range(len(items)) for j in cap if score[r, j] < direct_score[r]]
    share = np.zeros(score.shape)
    if not arcs:
        return share

    m = pulp.LpProblem("operate", pulp.LpMinimize)
    x = {a: pulp.LpVariable(f"x_{a[0]}_{a[1]}", lowBound=0, upBound=1) for a in arcs}
    m += pulp.lpSum(mult[r] * (score[r, j] - direct_score[r]) * x[r, j] for r, j in arcs)
    by_region = {}
    for r, j in arcs:
        by_region.setdefault(r, []).append(x[r, j])
    for xs in by_region.values():
        m += pulp.lpSum(xs) <= 1
    for j in cap:
        m += pulp.lpSum(items[r] * x[r, jj] for r, jj in arcs if jj == j) <= cap[j]
    m.solve(pulp.HiGHS(msg=False))
    for (r, j), v in x.items():
        share[r, j] = v.value() or 0.0
    return share


def operate(regions: Regions, built, multipliers: pd.Series, handling_fee=HANDLING_FEE, sizes=SIZES,
            carbon_price=0.0):
    """Route one future's demand through the buildings (see route_shares) and report the result:
    freight cost, share of items in two days, average transit days, share of items through a
    center and CO2, for that future.
    """
    C = regions.fc_cost(handling_fee)
    fc_co2, direct_co2 = regions.fc_co2(), regions.direct_co2()
    mult = np.array([multipliers[s] for s in regions.state])
    items = mult * regions.items
    share = route_shares(regions, built, multipliers, handling_fee, sizes, carbon_price)
    direct = 1 - share.sum(axis=1)
    cost = (mult[:, None] * share * C).sum() + (mult * direct * regions.direct_cost).sum()
    two_day = (mult[:, None] * share * regions.fc_two_day).sum() + (mult * direct * regions.direct_two_day).sum()
    days = (mult[:, None] * share * regions.fc_item_days).sum() + (mult * direct * regions.direct_item_days).sum()
    co2 = (mult[:, None] * share * fc_co2).sum() + (mult * direct * direct_co2).sum()
    total = items.sum()
    baseline = (mult * regions.direct_cost).sum()
    return {"cost": float(cost), "baseline_cost": float(baseline),
            "co2_t": float(co2 / 1000), "baseline_co2_t": float((mult * direct_co2).sum() / 1000),
            "saving_pct": float(100 * (1 - cost / baseline)),
            "two_day_pct": float(100 * two_day / total), "transit_days": float(days / total),
            "via_fc_pct": float(100 * (share.sum(axis=1) * items).sum() / total)}
