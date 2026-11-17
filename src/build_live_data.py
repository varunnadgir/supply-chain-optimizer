"""Export the model's inputs for the live optimizer on the web page.

The page (docs/index.html) solves the network model in the visitor's browser
with HiGHS compiled to WebAssembly (docs/vendor/). It needs the per-region
costs that src/stochastic_model.py works with. This script writes them to
docs/live_model.json.

    python src/build_live_data.py
"""

import json

import numpy as np

import network_model as nm
import stochastic_model as sm
from helpers import ROOT

PRUNE = 1.6     # leave out routes that cost more than 1.6 x shipping direct (never useful)


def main():
    data = nm.prepare()
    regions, _ = sm.aggregate(data)
    cities = data.cities.set_index("city_key")
    C = regions.fc_var_cost

    arcs = []
    for r in range(len(regions.items)):
        for j in range(len(regions.site_names)):
            if C[r, j] + sm.HANDLING_FEE * regions.items[r] < PRUNE * regions.direct_cost[r]:
                arcs.append([r, j, round(C[r, j], 2), round(regions.fc_two_day[r, j], 2),
                             round(regions.fc_item_days[r, j], 1), round(regions.fc_parcel_tkm[r, j], 3),
                             round(regions.fc_bulk_tkm[r, j], 3)])

    out = {
        "regions": [
            {"name": n, "state": st, "lat": round(float(cities.at[n, "lat"]), 3), "lng": round(float(cities.at[n, "lng"]), 3),
             "items": round(float(i), 2), "direct_cost": round(float(dc), 2), "direct_two_day": round(float(d2), 2),
             "direct_item_days": round(float(dd), 1), "direct_tkm": round(float(dt), 3)}
            for n, st, i, dc, d2, dd, dt in zip(regions.names, regions.state, regions.items, regions.direct_cost,
                                                  regions.direct_two_day, regions.direct_item_days,
                                                  regions.direct_parcel_tkm)
        ],
        "sites": [{"name": n, "lat": round(float(la), 3), "lng": round(float(lo), 3)}
                  for n, la, lo in zip(data.candidates["city_key"], data.candidates["lat"], data.candidates["lng"])],
        "arc_fields": ["region", "site", "freight_without_handling", "two_day_items", "item_days", "parcel_tkm", "bulk_tkm"],
        "arcs": arcs,
        "defaults": {"handling_fee": sm.HANDLING_FEE,
                     "sizes": sm.SIZES.reset_index(names="size").to_dict(orient="records")},
    }
    path = ROOT / "docs" / "live_model.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {path.relative_to(ROOT)}: {len(out['regions'])} regions, {len(out['sites'])} sites, "
          f"{len(arcs):,} routes, {path.stat().st_size / 1e3:,.0f} kB")


if __name__ == "__main__":
    main()
