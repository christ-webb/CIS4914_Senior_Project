// Renders each page of a running preview at the QA viewport widths.
// Usage: NM5_PREVIEW_URL=http://127.0.0.1:3001 NM5_TRACE_ID=<id> node scripts/capture-screens.mjs <outDir>
import { mkdirSync } from "node:fs";
import { chromium } from "playwright-core";

const base = process.env.NM5_PREVIEW_URL ?? "http://127.0.0.1:3001";
const outDir = process.argv[2] ?? "/tmp/nm5-screens";
const traceId = process.env.NM5_TRACE_ID;
const widths = [1440, 1280, 768, 390];
const pages = [["dashboard", "/"], ["explorer", "/explorer"], ["analytics", "/analytics"], ["errors", "/errors"]];
if (traceId) pages.push(["trace", `/traces/${traceId}`]);

mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.NM5_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" });
const report = [];
for (const width of widths) {
  const page = await browser.newPage({ viewport: { width, height: width < 500 ? 844 : 900 } });
  for (const [name, path] of pages) {
    await page.goto(base + path, { waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    const facts = await page.evaluate(() => ({
      h1: document.querySelector("h1")?.textContent?.trim() ?? null,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      font: getComputedStyle(document.querySelector("h1") ?? document.body).fontFamily,
    }));
    await page.screenshot({ path: `${outDir}/${name}-${width}.png`, fullPage: true });
    report.push({ page: name, width, ...facts });
  }
  await page.close();
}
const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await desktop.goto(base + "/", { waitUntil: "networkidle" });
await desktop.getByRole("link", { name: "Analytics", exact: true }).hover();
await desktop.locator(".active-run-row").first().hover();
await desktop.waitForTimeout(300);
await desktop.screenshot({ path: `${outDir}/state-hover-1440.png` });
await desktop.getByRole("button", { name: "Toggle navigation" }).click();
await desktop.waitForTimeout(300);
await desktop.screenshot({ path: `${outDir}/state-collapsed-1440.png` });
const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
await phone.goto(base + "/", { waitUntil: "networkidle" });
await phone.getByRole("button", { name: "Toggle navigation" }).click();
await phone.waitForTimeout(300);
await phone.screenshot({ path: `${outDir}/state-drawer-390.png` });
await browser.close();
console.log(JSON.stringify(report, null, 1));
