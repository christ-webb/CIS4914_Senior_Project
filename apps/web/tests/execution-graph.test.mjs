import assert from "node:assert/strict";
import test from "node:test";

import {
  FAILURE_SIBLING_RADIUS,
  assignStableGroupIds,
  buildVisibleGraph,
  classifyRelationships,
  consecutiveToolGroups,
  failureNeighborhood,
  groupsToExpandForFinding,
  remapExpandedGroups,
  remapSelection,
  sanitizeSpans,
  shouldFitGraph,
} from "../lib/execution-graph.ts";

const span = (id, extra = {}) => ({
  span_id: id,
  parent_span_id: extra.parent ?? null,
  type: extra.type ?? "tool",
  name: extra.name ?? id,
  started_at: extra.started_at ?? new Date(extra.start ?? 0).toISOString(),
  duration_ms: extra.duration ?? 10,
  status: extra.status ?? "ok",
  input: extra.input ?? null,
  output: extra.output ?? null,
  attributes: extra.attributes ?? {},
  token_count: extra.token_count ?? 0,
  cost_usd: extra.cost_usd ?? 0,
});

const finding = (id, spanIds) => ({
  finding_id: id,
  trace_id: "t1",
  span_ids: spanIds,
  detector_id: "tool_failure",
  detection_type: "tool_failure",
  severity: "warning",
  confidence: "heuristic",
  title: id,
  explanation: "observed",
  evidence: ["span status=error"],
  evidence_data: {},
  suggested_action: "inspect",
  first_observed_at: null,
  last_observed_at: null,
  detector_version: "1",
  provisional: false,
});

function cursorStar(tools) {
  return [span("agent", { type: "agent", name: "cursor", start: 0 }), ...tools];
}

test("consecutive Reads under one agent collapse in overview", () => {
  const spans = cursorStar([
    span("r1", { parent: "agent", name: "Read", start: 1 }),
    span("r2", { parent: "agent", name: "Read", start: 2 }),
    span("r3", { parent: "agent", name: "Read", start: 3 }),
    span("w1", { parent: "agent", name: "Write", start: 4 }),
  ]);
  const graph = buildVisibleGraph({ spans, mode: "overview" });
  assert.equal(graph.groups.length, 1);
  assert.deepEqual(graph.groups[0].memberIds, ["r1", "r2", "r3"]);
  assert.equal(graph.groups[0].memberIds.length, 3);
  assert.equal(graph.groups[0].durationMs, 30);
  assert.equal(graph.groups[0].durationIsWallClock, false);
  assert.equal(graph.nodes.filter((node) => node.kind === "group").length, 1);
  assert.ok(!graph.nodes.some((node) => node.id === "r1"));
});

test("expanding a group restores original span ids", () => {
  const spans = cursorStar([
    span("r1", { parent: "agent", name: "Read", start: 1 }),
    span("r2", { parent: "agent", name: "Read", start: 2 }),
  ]);
  const collapsed = buildVisibleGraph({ spans, mode: "overview" });
  const graph = buildVisibleGraph({
    spans,
    mode: "overview",
    expandedGroupIds: [collapsed.groups[0].id],
    previousGroups: collapsed.groups,
  });
  assert.ok(graph.nodes.some((node) => node.id === "r1"));
  assert.ok(graph.nodes.some((node) => node.id === "r2"));
  assert.equal(graph.nodes.filter((node) => node.kind === "group").length, 0);
});

test("collapsed group keeps failure and finding indicators", () => {
  const spans = cursorStar([
    span("s1", { parent: "agent", name: "Shell", start: 1 }),
    span("s2", { parent: "agent", name: "Shell", start: 2, status: "error" }),
    span("s3", { parent: "agent", name: "Shell", start: 3 }),
  ]);
  const findings = [finding("f1", ["s2"])];
  const graph = buildVisibleGraph({ spans, mode: "overview", findings, selectedFindingId: "f1" });
  const group = graph.groups[0];
  assert.equal(group.failedCount, 1);
  assert.equal(group.findingCount, 1);
  const node = graph.nodes.find((item) => item.kind === "group");
  assert.ok(node);
  assert.equal(groupsToExpandForFinding(graph.groups, findings[0])[0], group.id);
});

test("full mode keeps every span and verified parents only as parent edges", () => {
  const spans = [
    span("agent", { type: "agent", name: "cli", start: 0 }),
    span("llm", { parent: "agent", type: "llm", name: "model", start: 1, attributes: { "nm5.generation_id": "g1" } }),
    span("tool", { parent: "agent", name: "search", start: 2 }),
  ];
  const graph = buildVisibleGraph({ spans: [spans[2], spans[0], spans[1]], mode: "full" });
  assert.equal(graph.nodes.length, 3);
  assert.equal(graph.nodes.filter((node) => node.kind === "span").length, 3);
  const parentEdges = graph.edges.filter((edge) => edge.kind === "parent");
  const orderEdges = graph.edges.filter((edge) => edge.kind === "order");
  assert.equal(parentEdges.length, 2);
  assert.ok(parentEdges.every((edge) => edge.source === "agent"));
  assert.equal(orderEdges.length, 1);
  assert.equal(graph.edges.filter((edge) => edge.kind === "dependency").length, 0);
  assert.equal(graph.spanById.get("llm").attributes["nm5.generation_id"], "g1");
});

test("missing parents become chronological order, not verified parent edges", () => {
  const edges = classifyRelationships([span("a", { start: 0 }), span("b", { start: 1 }), span("c", { start: 2 })]);
  assert.equal(edges.filter((edge) => edge.kind === "parent").length, 0);
  assert.equal(edges.filter((edge) => edge.kind === "order").length, 2);
});

test("duplicate ids, bad timestamps, and parent cycles are sanitized", () => {
  const spans = [
    span("a", { start: 0 }),
    span("a", { start: 1, name: "dup" }),
    span("b", { parent: "c", started_at: "not-a-date" }),
    span("c", { parent: "b", start: 2 }),
  ];
  const clean = sanitizeSpans(spans);
  assert.equal(clean.filter((item) => item.span_id === "a").length, 1);
  assert.equal(clean.find((item) => item.span_id === "a").name, "a");
  const edges = classifyRelationships(spans);
  const parentEdges = edges.filter((edge) => edge.kind === "parent");
  assert.ok(parentEdges.length <= 1);
  const graph = buildVisibleGraph({ spans, mode: "full" });
  assert.equal(graph.nodes.length, 3);
});

test("failure focus keeps ancestors and nearby siblings, not the whole agent tree", () => {
  const tools = [];
  for (let i = 0; i < 20; i += 1) tools.push(span(`r${i}`, { parent: "agent", name: "Read", start: i + 1 }));
  tools.push(span("shell-fail", { parent: "agent", name: "Shell", start: 21, status: "error" }));
  for (let i = 0; i < 20; i += 1) tools.push(span(`w${i}`, { parent: "agent", name: "Write", start: 22 + i }));
  const spans = cursorStar(tools);
  const keep = failureNeighborhood(spans);
  assert.ok(keep.has("agent"));
  assert.ok(keep.has("shell-fail"));
  assert.ok(keep.has("r19"));
  assert.ok(keep.has("w0"));
  assert.ok(!keep.has("r0"));
  assert.ok(!keep.has("w19"));
  assert.ok(keep.size <= 2 + FAILURE_SIBLING_RADIUS * 2 + 1);
  const graph = buildVisibleGraph({ spans, mode: "failure" });
  assert.ok(graph.nodes.length < 10);
  assert.ok(!graph.nodes.some((node) => node.id === "r0"));
});

test("selecting a finding keeps all supporting span ids in the neighborhood", () => {
  const spans = cursorStar([
    span("r1", { parent: "agent", name: "Read", start: 1 }),
    span("s1", { parent: "agent", name: "Shell", start: 2, status: "error" }),
    span("s2", { parent: "agent", name: "Shell", start: 3 }),
  ]);
  const findings = [finding("f1", ["s1", "s2"])];
  const graph = buildVisibleGraph({ spans, mode: "failure", findings, selectedFindingId: "f1" });
  assert.ok(graph.nodes.some((node) => node.id === "s1"));
  assert.ok(graph.nodes.some((node) => node.id === "s2"));
});

test("group ids stay stable across append, out-of-order insert, merge, and split", () => {
  const first = consecutiveToolGroups(cursorStar([
    span("r1", { parent: "agent", name: "Read", start: 2 }),
    span("r2", { parent: "agent", name: "Read", start: 3 }),
  ]));
  const appended = consecutiveToolGroups(
    cursorStar([
      span("r1", { parent: "agent", name: "Read", start: 2 }),
      span("r2", { parent: "agent", name: "Read", start: 3 }),
      span("r3", { parent: "agent", name: "Read", start: 4 }),
    ]),
    [],
    first,
  );
  assert.equal(appended[0].id, first[0].id);

  const inserted = consecutiveToolGroups(
    cursorStar([
      span("r0", { parent: "agent", name: "Read", start: 1 }),
      span("r1", { parent: "agent", name: "Read", start: 2 }),
      span("r2", { parent: "agent", name: "Read", start: 3 }),
    ]),
    [],
    first,
  );
  assert.equal(inserted[0].id, first[0].id);

  const splitSource = consecutiveToolGroups(cursorStar([
    span("r1", { parent: "agent", name: "Read", start: 1 }),
    span("r2", { parent: "agent", name: "Read", start: 2 }),
    span("r3", { parent: "agent", name: "Read", start: 3 }),
    span("r4", { parent: "agent", name: "Read", start: 4 }),
  ]));
  const split = consecutiveToolGroups(
    cursorStar([
      span("r1", { parent: "agent", name: "Read", start: 1 }),
      span("r2", { parent: "agent", name: "Read", start: 2 }),
      span("w1", { parent: "agent", name: "Write", start: 3 }),
      span("r3", { parent: "agent", name: "Read", start: 4 }),
      span("r4", { parent: "agent", name: "Read", start: 5 }),
    ]),
    [],
    splitSource,
  );
  const readGroups = split.filter((group) => group.name === "Read");
  assert.equal(readGroups.length, 2);
  assert.ok(readGroups.some((group) => group.id === splitSource[0].id));
  const expanded = remapExpandedGroups(splitSource, split, [splitSource[0].id]);
  assert.equal(expanded.size, 2);

  const merged = consecutiveToolGroups(
    cursorStar([
      span("r1", { parent: "agent", name: "Read", start: 1 }),
      span("r2", { parent: "agent", name: "Read", start: 2 }),
      span("r3", { parent: "agent", name: "Read", start: 4 }),
      span("r4", { parent: "agent", name: "Read", start: 5 }),
    ]),
    [],
    split,
  );
  assert.equal(merged.length, 1);
  assert.ok(merged[0].id === readGroups[0].id || merged[0].id === readGroups[1].id);
  assert.equal(remapSelection(splitSource[0].id, splitSource, merged, new Set()), merged[0].id);
});

test("layout and grouping are deterministic after shuffling input", () => {
  const spans = cursorStar([
    span("r1", { parent: "agent", name: "Read", start: 1 }),
    span("r2", { parent: "agent", name: "Read", start: 2 }),
    span("w1", { parent: "agent", name: "Write", start: 3 }),
  ]);
  const shuffled = [spans[3], spans[1], spans[0], spans[2]];
  const a = buildVisibleGraph({ spans, mode: "overview" });
  const b = buildVisibleGraph({ spans: shuffled, mode: "overview" });
  assert.deepEqual(
    a.nodes.map((node) => node.id),
    b.nodes.map((node) => node.id),
  );
  assert.deepEqual(a.layout.positions, b.layout.positions);
  assert.equal(a.groups[0].id, b.groups[0].id);
});

test("overview does not bucket tools by nearby LLM generation timestamps", () => {
  const spans = cursorStar([
    span("llm", { parent: "agent", type: "llm", name: "cursor-turn", start: 1, attributes: { "nm5.generation_id": "g9" } }),
    span("r1", { parent: "agent", name: "Read", start: 2 }),
    span("r2", { parent: "agent", name: "Read", start: 3 }),
  ]);
  const graph = buildVisibleGraph({ spans, mode: "overview" });
  assert.ok(!graph.nodes.some((node) => node.id.includes("generation")));
  assert.equal(graph.spanById.get("llm").attributes["nm5.generation_id"], "g9");
  assert.equal(graph.groups[0].name, "Read");
});

test("shouldFitGraph ignores ordinary polling", () => {
  assert.equal(shouldFitGraph("initial"), true);
  assert.equal(shouldFitGraph("mode-change"), true);
  assert.equal(shouldFitGraph("finding-focus"), true);
  assert.equal(shouldFitGraph("poll"), false);
});

function manySpans(count) {
  const spans = [span("agent", { type: "agent", name: "agent", start: 0 })];
  for (let i = 0; i < count - 1; i += 1) {
    const name = i % 10 === 9 ? "Write" : "Read";
    spans.push(span(`t${i}`, { parent: "agent", name, start: i + 1, duration: 5 }));
  }
  return spans;
}

for (const count of [100, 500, 1000]) {
  test(`graph construction and layout stay usable at ${count} spans`, () => {
    const spans = manySpans(count);
    const start = Date.now();
    const overview = buildVisibleGraph({ spans, mode: "overview" });
    const full = buildVisibleGraph({ spans, mode: "full" });
    const elapsed = Date.now() - start;
    assert.equal(full.nodes.length, count);
    assert.ok(overview.nodes.length < count);
    assert.equal(Object.keys(full.layout.positions).length, count);
    assert.ok(elapsed < 750, `expected <750ms, took ${elapsed}ms`);
  });
}

test("sticky assignment prefers the larger overlapping run on split", () => {
  const prev = assignStableGroupIds([
    {
      parentId: "agent",
      type: "tool",
      name: "Read",
      memberIds: ["a", "b", "c", "d"],
      durationMs: 40,
      durationIsWallClock: false,
      statuses: { ok: 4, error: 0, unset: 0 },
      failedCount: 0,
      findingCount: 0,
      worstFindingSeverity: null,
    },
  ]);
  const next = assignStableGroupIds(
    [
      {
        parentId: "agent",
        type: "tool",
        name: "Read",
        memberIds: ["a", "b", "c"],
        durationMs: 30,
        durationIsWallClock: false,
        statuses: { ok: 3, error: 0, unset: 0 },
        failedCount: 0,
        findingCount: 0,
        worstFindingSeverity: null,
      },
      {
        parentId: "agent",
        type: "tool",
        name: "Read",
        memberIds: ["d", "e"],
        durationMs: 20,
        durationIsWallClock: false,
        statuses: { ok: 2, error: 0, unset: 0 },
        failedCount: 0,
        findingCount: 0,
        worstFindingSeverity: null,
      },
    ],
    prev,
  );
  assert.equal(next[0].id, prev[0].id);
  assert.notEqual(next[1].id, prev[0].id);
});
