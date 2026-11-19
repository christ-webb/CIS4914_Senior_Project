import { expect, test } from "@playwright/test";

for (const width of [1440, 1280, 768, 390]) {
  test(`stored dashboard geometry and navigation at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    await expect(page.getByRole("button", { name: /^Estimated cost/ })).toBeVisible();
    await expect(page.getByText(/Priced subset|Complete window coverage/)).toBeVisible();
    const geometry = await page.locator(".dashboard-kpis").evaluate(section => {
      const rect = section.getBoundingClientRect();
      return { top: rect.top, values: Array.from(section.querySelectorAll(".metric-value")).map(value => {
        const range = document.createRange(); range.selectNodeContents(value);
        const parent = value.closest(".card")!.getBoundingClientRect();
        const box = value.getBoundingClientRect();
        return { lines: range.getClientRects().length, fits: box.right <= parent.right - 8 && box.left >= parent.left };
      }) };
    });
    expect(geometry.top).toBeLessThan(width < 500 ? 450 : 300);
    expect(geometry.values).toHaveLength(5);
    for (const value of geometry.values) { expect(value.lines).toBe(1); expect(value.fits).toBe(true); }
    await expect(page.locator(".active-runs-list").first().locator(".active-run-row")).not.toHaveCount(6);
    const toggle = page.getByRole("button", { name: "Toggle navigation", exact: true });
    await expect(page.getByRole("link", { name: "Data Management" })).toHaveCount(0);
    if (width > 860) {
      const before = await page.locator("main").boundingBox();
      await expect(toggle.locator("svg")).toHaveAttribute("data-icon", "panel-left-close");
      await toggle.focus(); await page.keyboard.press("Enter");
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(toggle.locator("svg")).toHaveAttribute("data-icon", "panel-left-open");
      await expect.poll(async () => (await page.locator("main").boundingBox())!.width).toBeGreaterThan(before!.width);
      await page.getByRole("link", { name: "Analytics", exact: true }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Analytics" })).toBeVisible();
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await page.getByRole("link", { name: "Dashboard", exact: true }).click();
      await expect(page.getByRole("button", { name: /^Estimated cost/ })).toBeVisible();
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
    } else {
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await toggle.click();
      await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(toggle).toBeFocused();
      await expect(page.getByRole("navigation", { name: "Main" })).not.toBeVisible();
    }
    await expect(page.getByText(/Priced subset|Complete window coverage/)).toBeVisible();
    await page.screenshot({ path: info.outputPath(`dashboard-${width}.png`), fullPage: true });
  });
}

test("fixture, terminal and activity filtering preserves backend classification", async ({ page }) => {
  const base = { project_id: "qa", started_at: "2026-01-01", duration_ms: 210300, total_tokens: 25500000, total_cost_usd: 0, status: "unset", source: "cursor" };
  const records = [
    { ...base, trace_id: "active", name: "Executing now", lifecycle: "running", execution_state: "executing" },
    { ...base, trace_id: "idle", name: "Open conversation", lifecycle: "running", execution_state: "idle", last_turn_completed: true, turns_total: 1, turns_completed: 1, latest_turn_status: "completed", latest_turn_completed_at: "2026-01-04", task_outcome: "unverified" },
    { ...base, trace_id: "interrupted", name: "Interrupted session", lifecycle: "interrupted", execution_state: "unknown" },
    { ...base, trace_id: "fixture", name: "Explicit fixture", lifecycle: "running", execution_state: "executing", is_fixture: true },
    { ...base, trace_id: "older", name: "Older completed", lifecycle: "completed", ended_at: "2026-01-02" },
    { ...base, trace_id: "newer", name: "Newer cancelled", lifecycle: "cancelled", ended_at: "2026-01-03" },
  ];
  await page.route("**/v1/traces?*", route => route.fulfill({ json: records }));
  await page.goto("/");
  const active = page.getByRole("region", { name: "Active runs", exact: true });
  await expect(active.getByText("Executing now")).toBeVisible();
  await expect(active.getByText("Interrupted session")).toHaveCount(0);
  await expect(page.getByText("Explicit fixture")).toHaveCount(0);
  const idle = page.getByRole("region", { name: "Idle / Open", exact: true });
  await expect(idle.locator(".active-run-row").first()).toContainText("Open conversation");
  await expect(idle.locator(".active-run-row").first()).toContainText("Turn completed");
  const terminal = page.getByRole("region", { name: "Recently completed", exact: true });
  await expect(terminal.getByRole("button", { name: "Turns" })).toHaveAttribute("aria-pressed", "true");
  await expect(terminal.locator(".active-run-row").first()).toContainText("Open conversation");
  await expect(terminal.locator(".active-run-row").first()).toContainText("Idle / Open");
  await terminal.getByRole("button", { name: "Sessions" }).click();
  await expect(terminal.locator(".active-run-row").first()).toContainText("Newer cancelled");
  await expect(terminal.getByText("Open conversation")).toHaveCount(0);
});

test("analytics reports turn completion instead of a lifecycle success rate", async ({ page }) => {
  const base = { project_id: "qa", started_at: "2026-01-01", duration_ms: 1000, total_tokens: 0, total_cost_usd: 0, status: "unset", source: "cursor", trace_id: "readme", name: "README turn", lifecycle: "running", execution_state: "idle" };
  await page.route("**/v1/traces?*", route => route.fulfill({ json: [
    { ...base, turns_total: 1, turns_completed: 1, latest_turn_status: "completed", task_outcome: "unverified" },
  ] }));
  await page.route("**/v1/traces/readme", route => route.fulfill({ json: { ...base, spans: [], attributes: {
    "nm5.turns_total": 1, "nm5.turns_completed": 1, "nm5.turns_failed": 0, "nm5.turns_interrupted": 0,
    "nm5.latest_turn_status": "completed", "nm5.task_outcome_status": "unverified",
  } } }));
  await page.goto("/analytics");
  await expect(page.getByText("100%", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("0%", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Unverified", { exact: true }).first()).toBeVisible();
});

const pages = [["/", "Dashboard"], ["/explorer", "Explorer"], ["/analytics", "Analytics"], ["/errors", "Errors"]] as const;
for (const width of [1440, 1280, 768, 390]) {
  test(`editorial headings and no horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const [path, title] of pages) {
      await page.goto(path);
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toHaveText(title);
      expect((await heading.innerText()).trim().split(/\s+/).length).toBeLessThanOrEqual(2);
      expect(await heading.evaluate(h => getComputedStyle(h).fontFamily)).toContain("Barlow Condensed");
      expect(await page.locator(".page-head p").count()).toBe(0);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(1);
    }
  });
}

test("hover motion is disabled for reduced-motion users", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  const link = page.getByRole("link", { name: "Explorer", exact: true });
  await link.hover();
  expect(await link.locator(".nav-icon").evaluate(el => getComputedStyle(el).transform)).toBe("none");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.getByRole("link", { name: "Analytics", exact: true }).hover();
  await expect.poll(() => page.getByRole("link", { name: "Analytics", exact: true }).locator(".nav-icon").evaluate(el => getComputedStyle(el).transform)).not.toBe("none");
});

test("large and unavailable values fit without hiding exact disclosure", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route("**/v1/traces?*", route => route.fulfill({ json: [{
    trace_id: "large", project_id: "p", name: "Reported values", started_at: "2026-01-01", status: "unset",
    duration_ms: 210300, total_tokens: 25500000, token_status: "reported", cost_status: "unavailable",
    total_cost_usd: 0, estimated_cost_status: "partial", estimated_subtotal_usd: 3.301884,
  }] }));
  await page.goto("/");
  await expect(page.getByText("Actual billed: Not reported", { exact: true })).toBeVisible();
  await expect(page.getByText("25.5M", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^Estimated cost/ }).focus();
  await expect(page.getByText("Exact subtotal: 3.301884–3.301884 USD.", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "Estimated cost details" })).not.toBeVisible();
});

test("reset generation invalidates already rendered history", async ({ page }) => {
  let generation = "0";
  await page.route("**/health", route => route.fulfill({ json: { status: "ok" }, headers: { "X-NM5-History-Generation": generation, "Access-Control-Expose-Headers": "X-NM5-History-Generation" } }));
  await page.route("**/v1/traces?*", route => route.fulfill({ headers: { "X-NM5-History-Generation": generation, "Access-Control-Expose-Headers": "X-NM5-History-Generation" }, json: generation === "1" ? [] : [{
    trace_id: "old", project_id: "p", name: "History before reset", started_at: "2026-01-01", status: "ok",
    lifecycle: "completed", ended_at: "2026-01-02", duration_ms: 100, total_tokens: 0, total_cost_usd: 0,
  }] }));
  await page.goto("/");
  await expect(page.getByText("History before reset")).toBeVisible();
  generation = "1";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("History before reset")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Explore all records, including fixtures" })).toBeVisible();
});
