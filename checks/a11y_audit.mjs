// Headless accessibility audit: loads a page in Chromium and runs axe-core.
// Fails (exit 1) if there are any critical/serious violations.
//
//   cd checks && npm ci
//   node checks/a11y_audit.mjs path/to/index.html
//
// Wire it into a spec's `verification` to make accessibility a hard gate.

import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { pathToFileURL } from "node:url";
import path from "node:path";

const target = process.argv[2] || "index.html";
const url = pathToFileURL(path.resolve(target)).href;

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: "load" });

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();

  const blocking = results.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious"
  );

  for (const v of results.violations) {
    const mark = blocking.includes(v) ? "✗" : "·";
    console.log(`${mark} [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node(s))`);
  }

  if (blocking.length) {
    console.error(`a11y FAIL: ${blocking.length} critical/serious violation(s)`);
    process.exit(1);
  }
  console.log("a11y ok");
} finally {
  await browser.close();
}
