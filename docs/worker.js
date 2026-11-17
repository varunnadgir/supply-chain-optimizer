// Runs the optimizer off the main thread so the page stays responsive while HiGHS solves.
import loadHighs from "./vendor/highs.mjs";
import { buildModel, readSolution } from "./optimizer.js";

let data = null;
let highsPromise = null;
const highs = () => (highsPromise ??= loadHighs({ locateFile: (f) => new URL("./vendor/" + f, import.meta.url).href }));

self.onmessage = async ({ data: msg }) => {
  if (msg.type === "data") { data = msg.data; return; }
  const { id, params } = msg;
  const p = { ...params, excluded: new Set(params.excluded) };
  const t0 = performance.now();
  try {
    const solver = await highs();
    const model = buildModel(data, p);
    const result = solver.solve(model.text, { output_flag: false, mip_rel_gap: 0.003, time_limit: 20 });
    if (result.Status !== "Optimal" && result.Status !== "Time limit reached") {
      self.postMessage({ id, error: result.Status === "Infeasible"
        ? "No network can meet these settings. Try a lower delivery promise or allow more centers."
        : `The solver stopped with status: ${result.Status}` });
      return;
    }
    const solution = readSolution(data, p, model, result);
    solution.status = result.Status;
    solution.ms = performance.now() - t0;
    solution.vars = model.text.split("\n").length;
    self.postMessage({ id, solution });
  } catch (e) {
    highsPromise = null;               // a crashed WebAssembly instance cannot be reused
    self.postMessage({ id, error: "The solver failed on these settings. Try again or change a setting." });
  }
};
