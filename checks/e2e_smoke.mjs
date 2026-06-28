// Playwright e2e smoke against the RUNNING app. Use as a server-backed check:
//   verification:
//     - name: e2e
//       command: "node ../../checks/e2e_smoke.mjs"
//       needs_server: true
// The harness boots the dev server and substitutes $APP_URL; this script reads it
// (falling back to a CLI arg or localhost). Fails on console errors, page errors,
// or a blank page. Needs Node + Playwright + Chromium (see checks/README.md).
import { chromium } from "playwright";

const url = process.env.APP_URL || process.argv[2] || "http://127.0.0.1:3000";
const errors = [];

const browser = await chromium.launch();
const page = await browser.newPage();
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));

try {
  const resp = await page.goto(url, { waitUntil: "networkidle", timeout: 20000 });
  if (!resp || !resp.ok()) throw new Error(`GET ${url} -> ${resp ? resp.status() : "no response"}`);
  // The app should render *something* visible.
  const text = (await page.locator("body").innerText()).trim();
  if (text.length < 1 && (await page.locator("canvas, svg, img").count()) === 0) {
    throw new Error("page rendered blank (no text/visual content)");
  }
  if (errors.length) throw new Error("runtime errors:\n  " + errors.join("\n  "));
  console.log(`e2e smoke OK: ${url} rendered, no console/page errors`);
} catch (err) {
  console.error("e2e smoke FAILED:", err.message);
  process.exitCode = 1;
} finally {
  await browser.close();
}
