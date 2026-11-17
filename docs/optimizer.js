// The network model of notebook 5, for one future, written for the browser.
//
// buildModel() turns the data in live_model.json and the visitor's settings into
// a mixed-integer program in the plain-text "LP format" that HiGHS reads.
// readSolution() turns HiGHS's answer back into sites, routes and totals.
// Same model as src/stochastic_model.py with a single future.

// Emission factors (kg CO2e per tonne-km of product moved); see notebook 7.
export const PARCEL_KG_CO2_PER_TKM = 0.25;   // parcel network: vans and part-loaded trucks
export const TRUCK_KG_CO2_PER_TKM = 0.10;    // full truck, seller to center

export function buildModel(data, p) {
  // p: {handlingFee, growth, sizes: [{size, fixed_cost, capacity}], useCapacity,
  //     maxCenters, twoDayShare, carbonPrice (R$ per tonne), excluded: Set of site numbers}
  const R = data.regions, S = data.sites, arcs = data.arcs;
  const g = p.growth;
  const carbon = (p.carbonPrice || 0) / 1000;           // R$ per kg CO2
  const terms = [];
  const sitesUsed = new Set();
  const arcsOfRegion = R.map(() => []);
  const arcsOfSite = S.map(() => []);

  arcs.forEach((a, n) => {
    const [r, j, freight, twoDay, , parcelTkm, bulkTkm] = a;
    if (p.excluded && p.excluded.has(j)) return;
    const co2 = PARCEL_KG_CO2_PER_TKM * parcelTkm + TRUCK_KG_CO2_PER_TKM * bulkTkm;
    const directCo2 = PARCEL_KG_CO2_PER_TKM * R[r].direct_tkm;
    const cost = freight + p.handlingFee * R[r].items + carbon * co2;
    const direct = R[r].direct_cost + carbon * directCo2;
    // A route that costs more than shipping direct is never used, unless it helps meet the two-day promise
    if (cost >= direct && !(p.twoDayShare != null && twoDay > R[r].direct_two_day)) return;
    terms.push(`${fmt(g * cost)} x${n}`);
    arcsOfRegion[r].push(n);
    arcsOfSite[j].push(n);
    sitesUsed.add(j);
  });
  R.forEach((reg, r) => {
    const co2 = PARCEL_KG_CO2_PER_TKM * reg.direct_tkm;
    terms.push(`${fmt(g * (reg.direct_cost + carbon * co2))} d${r}`);
  });
  const sizes = p.useCapacity ? p.sizes : [p.sizes[0]];
  const built = j => sizes.map((s, k) => `y${j}_${k}`);
  for (const j of sitesUsed) sizes.forEach((s, k) => terms.push(`${fmt(s.fixed_cost)} y${j}_${k}`));

  const lines = ["Minimize", " obj:", chunk(terms), "Subject To"];
  let c = 0;
  R.forEach((reg, r) => lines.push(` c${c++}: ` + [...arcsOfRegion[r].map(n => `x${n}`), `d${r}`].join(" + ") + " = 1"));
  for (const j of sitesUsed) {
    lines.push(` c${c++}: ` + built(j).join(" + ") + " <= 1");            // one building per site
    for (const n of arcsOfSite[j]) lines.push(` c${c++}: x${n} - ` + built(j).join(" - ") + " <= 0");  // only if built
    if (p.useCapacity) {
      const load = arcsOfSite[j].map(n => `${fmt(g * R[arcs[n][0]].items)} x${n}`);
      const cap = sizes.map((s, k) => `${fmt(s.capacity)} y${j}_${k}`);
      lines.push(` c${c++}:` + chunk(load) + "\n - " + cap.join(" - ") + " <= 0");
    }
  }
  if (p.maxCenters != null) {
    const all = [...sitesUsed].flatMap(built);
    lines.push(` c${c++}:` + chunk(all) + ` <= ${p.maxCenters}`);
  }
  if (p.twoDayShare != null) {
    const t = [];
    R.forEach((reg, r) => {
      for (const n of arcsOfRegion[r]) t.push(`${fmt(g * arcs[n][3])} x${n}`);
      t.push(`${fmt(g * reg.direct_two_day)} d${r}`);
    });
    const total = g * R.reduce((s, reg) => s + reg.items, 0);
    lines.push(` c${c++}:` + chunk(t) + ` >= ${fmt(p.twoDayShare * total)}`);
  }
  const binaries = [...sitesUsed].flatMap(built);
  lines.push("Binary");
  for (let i = 0; i < binaries.length; i += 8) lines.push(" " + binaries.slice(i, i + 8).join(" "));
  lines.push("End");
  return { text: lines.join("\n"), sizes, arcsOfRegion };
}

export function readSolution(data, p, model, result) {
  const R = data.regions, arcs = data.arcs, g = p.growth, cols = result.Columns;
  const val = name => (cols[name] ? cols[name].Primal : 0);
  const built = [];
  data.sites.forEach((s, j) => model.sizes.forEach((sz, k) => {
    if (val(`y${j}_${k}`) > 0.5) built.push({ site: j, size: sz.size, fixed_cost: sz.fixed_cost, capacity: sz.capacity, load: 0 });
  }));
  const bySite = new Map(built.map(b => [b.site, b]));
  const t = { freight: 0, twoDay: 0, itemDays: 0, viaFc: 0, co2: 0, items: 0, baseline: 0, baseCo2: 0, baseDays: 0, baseTwo: 0 };
  const regionSite = R.map(() => -1);
  R.forEach((reg, r) => {
    let rest = 1, best = 0;
    for (const n of model.arcsOfRegion[r]) {
      const x = val(`x${n}`);
      if (x < 1e-6) continue;
      const a = arcs[n];
      t.freight += g * x * (a[2] + p.handlingFee * reg.items);
      t.twoDay += g * x * a[3];
      t.itemDays += g * x * a[4];
      t.viaFc += g * x * reg.items;
      t.co2 += g * x * (PARCEL_KG_CO2_PER_TKM * a[5] + TRUCK_KG_CO2_PER_TKM * a[6]);
      bySite.get(a[1]).load += g * x * reg.items;
      if (x > best) { best = x; regionSite[r] = a[1]; }
      rest -= x;
    }
    rest = Math.max(0, rest);
    if (rest > best) regionSite[r] = -1;
    t.freight += g * rest * reg.direct_cost;
    t.twoDay += g * rest * reg.direct_two_day;
    t.itemDays += g * rest * reg.direct_item_days;
    t.co2 += g * rest * PARCEL_KG_CO2_PER_TKM * reg.direct_tkm;
    t.items += g * reg.items;
    t.baseline += g * reg.direct_cost;
    t.baseCo2 += g * PARCEL_KG_CO2_PER_TKM * reg.direct_tkm;
    t.baseDays += g * reg.direct_item_days;
    t.baseTwo += g * reg.direct_two_day;
  });
  const fixed = built.reduce((s, b) => s + b.fixed_cost, 0);
  return {
    built, regionSite, fixed, freight: t.freight, total: fixed + t.freight, baseline: t.baseline, items: t.items,
    saving: t.baseline - fixed - t.freight, savingPct: 100 * (1 - (fixed + t.freight) / t.baseline),
    twoDayPct: 100 * t.twoDay / t.items, baseTwoDayPct: 100 * t.baseTwo / t.items,
    days: t.itemDays / t.items, baseDays: t.baseDays / t.items,
    viaFcPct: 100 * t.viaFc / t.items, co2Tonnes: t.co2 / 1000, baseCo2Tonnes: t.baseCo2 / 1000,
  };
}

function fmt(v) { return Number.isInteger(v) ? String(v) : v.toFixed(4); }

function chunk(terms) {            // LP format lines must stay short: break long sums over lines
  const out = [];
  for (let i = 0; i < terms.length; i += 8) out.push((i ? " + " : " ") + terms.slice(i, i + 8).join(" + "));
  return "\n" + out.join("\n");
}
