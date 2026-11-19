import assert from "node:assert/strict";
import test from "node:test";

import {
  buildTraceListPath,
  hasActiveFilters,
  historyEmptyKind,
  paginateWindow,
} from "../lib/history.ts";

test("buildTraceListPath keeps an unfiltered list contract", () => {
  assert.equal(buildTraceListPath(), "/v1/traces?fixtures=exclude");
});

test("buildTraceListPath encodes filters and pagination", () => {
  assert.equal(
    buildTraceListPath({
      project_id: "demo",
      status: "error",
      started_after: "2026-01-01T00:00:00.000Z",
      limit: 21,
      offset: 20,
      lifecycle: "running",
    }),
    "/v1/traces?fixtures=exclude&project_id=demo&status=error&lifecycle=running&started_after=2026-01-01T00%3A00%3A00.000Z&limit=21&offset=20",
  );
});

test("paginateWindow uses limit-plus-one for next-page detection", () => {
  const rows = [1, 2, 3];
  assert.deepEqual(paginateWindow(rows, 2), { items: [1, 2], hasMore: true });
  assert.deepEqual(paginateWindow([1, 2], 2), { items: [1, 2], hasMore: false });
});

test("historyEmptyKind distinguishes empty, filtered, and unavailable", () => {
  assert.equal(historyEmptyKind({ online: false, itemCount: 0, hasFilters: false }), "unavailable");
  assert.equal(historyEmptyKind({ online: true, itemCount: 0, hasFilters: false }), "empty");
  assert.equal(historyEmptyKind({ online: true, itemCount: 0, hasFilters: true }), "no-match");
  assert.equal(historyEmptyKind({ online: true, itemCount: 2, hasFilters: true }), null);
  assert.equal(historyEmptyKind({ online: false, loading: true, itemCount: 0, hasFilters: false }), null);
});

test("hasActiveFilters ignores pagination-only params", () => {
  assert.equal(hasActiveFilters({ limit: 21, offset: 20 }), false);
  assert.equal(hasActiveFilters({ project_id: "demo" }), true);
});
