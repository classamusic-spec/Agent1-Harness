// Lighthouse audit with score budgets. Serves the file's directory over a tiny
// static server (Lighthouse wants http), runs Lighthouse in headless Chrome,
// and fails if performance/accessibility/best-practices fall below budget.
//
//   cd checks && npm ci
//   node checks/lighthouse_audit.mjs path/to/index.html
//
// Budgets (override via env): LH_PERF=0.8 LH_A11Y=0.9 LH_BP=0.9

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { launch } from "chrome-launcher";
import lighthouse from "lighthouse";

const target = process.argv[2] || "index.html";
const dir = path.dirname(path.resolve(target));
const file = path.basename(target);

const MIME = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
  ".json": "application/json", ".svg": "image/svg+xml" };

const server = http.createServer((req, res) => {
  const rel = decodeURIComponent((req.url || "/").split("?")[0]);
  const fp = path.join(dir, rel === "/" ? file : rel);
  if (!fp.startsWith(dir) || !fs.existsSync(fp)) { res.statusCode = 404; return res.end("nf"); }
  res.setHeader("Content-Type", MIME[path.extname(fp)] || "application/octet-stream");
  fs.createReadStream(fp).pipe(res);
});

await new Promise((r) => server.listen(0, r));
const port = server.address().port;
const chrome = await launch({ chromeFlags: ["--headless=new", "--no-sandbox"] });

try {
  const { lhr } = await lighthouse(`http://localhost:${port}/`, {
    port: chrome.port,
    onlyCategories: ["performance", "accessibility", "best-practices"],
    output: "json",
  });

  const budgets = {
    performance: Number(process.env.LH_PERF || 0.8),
    accessibility: Number(process.env.LH_A11Y || 0.9),
    "best-practices": Number(process.env.LH_BP || 0.9),
  };

  let failed = false;
  for (const [cat, min] of Object.entries(budgets)) {
    const score = lhr.categories[cat]?.score ?? 0;
    const ok = score >= min;
    failed = failed || !ok;
    console.log(`${ok ? "✓" : "✗"} ${cat}: ${(score * 100).toFixed(0)} (budget ${min * 100})`);
  }
  process.exitCode = failed ? 1 : 0;
} finally {
  await chrome.kill();
  server.close();
}
