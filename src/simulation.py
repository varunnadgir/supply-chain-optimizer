"""A day-by-day simulation of the six-center network (notebook 6).

Notebooks 2 and 3 work with yearly totals and weekly averages. This module
replays the real order stream, one item at a time, through the network chosen
in notebook 2, run with the inventory policy of notebook 3. It is a
discrete-event simulation written with SimPy: every order, every daily picking
wave and every replenishment truck is an event on a clock.

What happens to one item ordered by a customer:

  1. If the customer's city is not served by a center, or the item's category
     is not stocked there, the seller ships it direct, as today.
  2. If the center has the item in stock, it joins the center's picking queue.
     Each day the center picks and ships up to its daily capacity, oldest
     orders first. Then the parcel spends a random number of days with the
     carrier, drawn from real transit times for that distance.
  3. If the center is out of stock, the seller ships it direct (a stockout).

Every Monday each center reviews each category it stocks: it updates its
forecast and orders enough to bring stock on hand plus stock on order up to
the order-up-to level of notebook 3. The truck arrives after a random lead time.

Times are in days since the simulation start. Direct items keep their real
purchase-to-door time from the data, so "today" is exactly what happened.
"""

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import simpy
from scipy.stats import norm

from helpers import RESULTS
from inventory_model import ALPHA, LEAD_WEEKS, REVIEW_WEEKS
from network_model import HANDLING_FEE, NetworkData


@dataclass
class Settings:
    start: str = "2017-10-02"           # a Monday; September is used to start the forecasts
    end: str = "2018-08-20"             # the last complete week in the data
    service_level: float = 0.95
    lead_days: tuple = (4, 7, 12)       # replenishment lead time: triangular (min, most likely, max)
    capacity_headroom: float = 0.30     # daily picking capacity = (1 + headroom) x average daily volume
    stocked: str = "policy"             # "policy" = the 10 categories of notebook 3, "all" = every category
    seed: int = 0


@dataclass
class Center:
    name: str
    site: int                           # column in NetworkData arrays
    capacity: float = 0.0               # items picked per day
    stock: dict = field(default_factory=dict)
    on_order: dict = field(default_factory=dict)
    forecast: dict = field(default_factory=dict)
    sigma: dict = field(default_factory=dict)
    week_demand: dict = field(default_factory=dict)
    queue: deque = field(default_factory=deque)


def run(data: NetworkData, settings: Settings = Settings()):
    """Simulate one run. Returns (items, daily): one row per item with its outcome,
    and one row per center per day with stock, backlog and stockouts."""
    rng = np.random.default_rng(settings.seed)
    start = pd.Timestamp(settings.start)
    ship = data.ship
    t_order = (pd.to_datetime(ship["purchased_at"]) - start).dt.total_seconds().to_numpy() / 86400
    horizon = (pd.Timestamp(settings.end) - start).days
    in_window = (t_order >= 0) & (t_order < horizon)

    # The network of notebook 2 and the policy of notebook 3
    assignments = pd.read_csv(RESULTS / "city_assignments.csv")
    served_by = dict(zip(assignments["city_key"], assignments["served_by"]))
    policy = pd.read_csv(RESULTS / "inventory_policy.csv")
    site_number = {name: j for j, name in enumerate(data.candidates["city_key"])}
    centers = {name: Center(name, site_number[name]) for name in policy["served_by"].unique()}

    center_of_item = ship["city_key"].map(served_by).where(lambda s: s.isin(centers.keys()))
    if settings.stocked == "all":
        stocked = {name: set(ship["category"].unique()) for name in centers}
    else:
        stocked = {name: set(g["category"]) for name, g in policy.groupby("served_by")}
    is_stocked = np.array([c in stocked.get(n, ()) if isinstance(n, str) else False
                           for n, c in zip(center_of_item, ship["category"])])

    # Weekly demand history per (center, category), used to start the forecasts
    weeks_before = (t_order < 0) & (t_order >= -28) & is_stocked
    z = norm.ppf(settings.service_level)
    sigma_of = {(r.served_by, r.category): r.sigma for r in policy.itertuples()}
    for name, center in centers.items():
        for cat in stocked[name]:
            mask = weeks_before & (center_of_item == name).to_numpy() & (ship["category"] == cat).to_numpy()
            weekly_avg = mask.sum() / 4
            center.forecast[cat] = weekly_avg
            # notebook 3's forecast error where it exists; otherwise a Poisson-like guess
            center.sigma[cat] = sigma_of.get((name, cat), max(1.0, np.sqrt(weekly_avg)))
            center.stock[cat] = order_up_to(center, cat, z)
            center.on_order[cat] = 0.0
            center.week_demand[cat] = 0
        volume = (in_window & is_stocked & (center_of_item == name).to_numpy()).sum()
        center.capacity = np.ceil((1 + settings.capacity_headroom) * volume / horizon)

    # Average selling price per category, to value the stock on hand
    price = ship.groupby("category")["price"].mean().to_dict()

    # Carrier transit times observed in the data, by distance zone, to draw from
    transit_by_zone = {z_: g.dropna().to_numpy() for z_, g in ship.loc[ship["transit_days"] > 0]
                       .groupby("zone")["transit_days"]}
    cities = ship["city_i"].to_numpy()
    zone_from_site = data.zone_city_site  # [city, site]

    # Outcome columns, filled in by the simulation
    n = len(ship)
    route = np.full(n, "", dtype=object)
    door = np.full(n, np.nan)
    cost = np.full(n, np.nan)
    daily = []

    env = simpy.Environment()

    def fc_cost(i, j):
        zone = zone_from_site[cities[i], j]
        kg = ship["weight_kg"].iat[i]
        return (data.base[zone] + data.rate[zone] * kg + HANDLING_FEE
                + data.linehaul_rate * kg * data.seller_to_site_km[i, j])

    def ship_direct(i, why):
        route[i] = why
        door[i] = ship["door_days"].iat[i]
        cost[i] = ship["predicted_freight"].iat[i]

    def customers():
        for i in np.argsort(t_order):
            if not in_window[i]:
                continue
            yield env.timeout(max(0.0, t_order[i] - env.now))
            name = center_of_item.iat[i]
            if not isinstance(name, str):
                ship_direct(i, "direct: city not served")
                continue
            cat = ship["category"].iat[i]
            if cat not in stocked[name]:
                ship_direct(i, "direct: category not stocked")
                continue
            center = centers[name]
            center.week_demand[cat] += 1
            if center.stock[cat] >= 1:
                center.stock[cat] -= 1
                center.queue.append((i, env.now))
            else:
                ship_direct(i, "direct: stockout")

    def picking(center):
        while True:
            yield env.timeout(1.0 - (env.now % 1.0))           # end of each day
            picked = 0
            while center.queue and picked < center.capacity:
                i, ordered_at = center.queue.popleft()
                zone = zone_from_site[cities[i], center.site]
                route[i] = "center"
                door[i] = (env.now - ordered_at) + rng.choice(transit_by_zone[zone])
                cost[i] = fc_cost(i, center.site)
                picked += 1
            daily.append({"day": env.now, "center": center.name, "backlog": len(center.queue),
                          "stock": sum(center.stock.values()),
                          "stock_value": sum(units * price[cat] for cat, units in center.stock.items())})

    def replenishment(center):
        while True:
            yield env.timeout(7.0 - (env.now % 7.0))           # every Monday
            for cat in center.forecast:
                demand = center.week_demand[cat]
                center.forecast[cat] = ALPHA * demand + (1 - ALPHA) * center.forecast[cat]
                center.week_demand[cat] = 0
                position = center.stock[cat] + center.on_order[cat]
                quantity = max(0.0, order_up_to(center, cat, z) - position)
                if quantity > 0:
                    center.on_order[cat] += quantity
                    env.process(truck(center, cat, quantity))

    def truck(center, cat, quantity):
        yield env.timeout(rng.triangular(*settings.lead_days))
        center.stock[cat] += quantity
        center.on_order[cat] -= quantity

    env.process(customers())
    for center in centers.values():
        env.process(picking(center))
        env.process(replenishment(center))
    env.run(until=horizon + 30)                                 # extra days to clear the last queues

    items = ship.loc[in_window, ["city_key", "category", "door_days", "predicted_freight"]].copy()
    items["center"] = center_of_item[in_window]
    items["route"] = route[in_window]
    items["sim_door_days"] = door[in_window]
    items["sim_cost"] = cost[in_window]
    items["ordered"] = start + pd.to_timedelta(t_order[in_window], unit="D")
    daily = pd.DataFrame(daily)
    daily["date"] = start + pd.to_timedelta(daily["day"], unit="D")
    return items, daily


def order_up_to(center: Center, cat: str, z: float) -> float:
    """S = forecast x (R + L) + z x sigma x sqrt(R + L), as in notebook 3 (rounded up)."""
    weeks = REVIEW_WEEKS + LEAD_WEEKS
    return float(np.ceil(center.forecast[cat] * weeks + z * center.sigma[cat] * np.sqrt(weeks)))


def summarize(items: pd.DataFrame) -> dict:
    """Headline numbers of one run, next to what actually happened."""
    door_today = items["door_days"].fillna(items["door_days"].median())
    sim_door = items["sim_door_days"].fillna(door_today)
    served = items["center"].notna()
    stocked = served & ~items["route"].eq("direct: category not stocked")
    return {
        "items": len(items),
        "via_center_pct": 100 * items["route"].eq("center").mean(),
        "fill_rate_pct": 100 * items.loc[stocked, "route"].eq("center").mean(),
        "door_days_today": door_today.mean(),
        "door_days_sim": sim_door.mean(),
        "p90_door_today": door_today.quantile(0.9),
        "p90_door_sim": sim_door.quantile(0.9),
        "within_5_days_today_pct": 100 * (door_today <= 5).mean(),
        "within_5_days_sim_pct": 100 * (sim_door <= 5).mean(),
        "freight_today": items["predicted_freight"].sum(),
        "freight_sim": items["sim_cost"].sum(),
        "saving_pct": 100 * (1 - items["sim_cost"].sum() / items["predicted_freight"].sum()),
    }
