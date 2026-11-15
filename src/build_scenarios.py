"""Precompute every scenario shown on the interactive page (docs/index.html).

The optimizer needs a solver, which cannot run in a web browser. So this
script solves the model for every combination the page offers ahead of time
and saves the answers to docs/scenarios.json. The page then just looks them up.

    python src/build_scenarios.py

Scenarios: 1 to 8 fulfillment centers x handling fee R$0 to R$6 per item
(56 solves). Uses all CPU cores; takes a few minutes.
"""

import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import network_model as nm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "scenarios.json"

N_CENTERS = range(1, 9)
HANDLING_FEES = [0, 1, 2, 3, 4, 5, 6]

DATA = None  # filled in once per worker process


def _init_worker():
    global DATA
    DATA = nm.prepare()


def _solve(args):
    n_open, fee = args
    r = nm.best_network(DATA, n_open, handling_fee=fee)
    return {
        "centers": n_open,
        "handling_fee": fee,
        "saving": round(r["saving"]),
        "saving_pct": round(r["saving_pct"], 2),
        "transit_days": round(r["transit_days"], 2),
        "pct_items_via_fc": round(r["pct_items_via_fc"], 2),
        "sites": r["sites"],
        "assigned": [int(s) for s in r["assigned_site"]],
    }


def main():
    data = nm.prepare()
    jobs = [(n, fee) for n in N_CENTERS for fee in HANDLING_FEES]
    with Pool(processes=os.cpu_count(), initializer=_init_worker) as pool:
        scenarios = []
        for k, result in enumerate(pool.imap_unordered(_solve, jobs), start=1):
            scenarios.append(result)
            print(f"{k:2d}/{len(jobs)}  centers={result['centers']}  fee=R${result['handling_fee']}  "
                  f"saving={result['saving_pct']:.1f}%")
    scenarios.sort(key=lambda s: (s["centers"], s["handling_fee"]))

    cities = data.cities
    payload = {
        "baseline": {
            "freight": round(data.baseline_cost),
            "transit_days": round(data.baseline_transit, 2),
            "items": int(data.items_per_city.sum()),
        },
        "cities": {
            "name": [k.split(",")[0].title() for k in cities["city_key"]],
            "state": cities["state"].tolist(),
            "lat": cities["lat"].round(3).tolist(),
            "lng": cities["lng"].round(3).tolist(),
            "items": cities["items"].astype(int).tolist(),
        },
        "sites": {
            "name": [k.split(",")[0].title() for k in data.candidates["city_key"]],
            "state": data.candidates["state"].tolist(),
            "lat": data.candidates["lat"].round(3).tolist(),
            "lng": data.candidates["lng"].round(3).tolist(),
        },
        "scenarios": scenarios,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"\nWrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
