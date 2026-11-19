import { findingsForSpan } from "./diagnostics.ts";
import { layoutGraph, type SpanLayout } from "./layout.ts";
import type { DiagnosticFinding, FindingSeverity, Span, SpanStatus, SpanType } from "./types.ts";

export type GraphMode = "overview" | "full" | "failure";
export type RelationshipKind = "parent" | "order" | "dependency";
export type GraphNodeKind = "span" | "group";

export const FAILURE_SIBLING_RADIUS = 2;
export const FULL_TRACE_VIRTUALIZE_AFTER = 200;

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: RelationshipKind;
}

export interface SpanGroup {
  id: string;
  parentId: string | null;
  type: SpanType;
  name: string;
  memberIds: string[];
  durationMs: number;
  durationIsWallClock: false;
  statuses: Record<SpanStatus, number>;
  failedCount: number;
  findingCount: number;
  worstFindingSeverity: FindingSeverity | null;
}

export interface VisibleNode {
  id: string;
  kind: GraphNodeKind;
  span: Span | null;
  group: SpanGroup | null;
  sortKey: string;
}

export interface VisibleGraph {
  nodes: VisibleNode[];
  edges: GraphEdge[];
  groups: SpanGroup[];
  layout: SpanLayout;
  spanById: Map<string, Span>;
}

export interface VisibleGraphOptions {
  spans: Span[];
  mode: GraphMode;
  expandedGroupIds?: Iterable<string>;
  previousGroups?: SpanGroup[];
  findings?: DiagnosticFinding[];
  selectedFindingId?: string | null;
}

const STATUS_ZERO: Record<SpanStatus, number> = { unset: 0, ok: 0, error: 0 };
const SEVERITY_RANK: Record<FindingSeverity, number> = { critical: 3, warning: 2, info: 1 };

export function spanSortKey(span: Span): string {
  const ts = parseTime(span.started_at);
  const timePart = ts === null ? "9999999999999" : String(ts).padStart(13, "0");
  return `${timePart}:${span.span_id}`;
}

export function parseTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : null;
}

export function sanitizeSpans(spans: Span[]): Span[] {
  const seen = new Set<string>();
  const unique: Span[] = [];
  for (const span of spans) {
    if (!span?.span_id || seen.has(span.span_id)) continue;
    seen.add(span.span_id);
    unique.push(span);
  }
  return unique.slice().sort((a, b) => spanSortKey(a).localeCompare(spanSortKey(b)));
}

export function verifiedParentOf(span: Span, ids: Set<string>): string | null {
  const parent = span.parent_span_id;
  if (!parent || parent === span.span_id || !ids.has(parent)) return null;
  return parent;
}

export function parentMap(spans: Span[]): Map<string, string> {
  const ids = new Set(spans.map((span) => span.span_id));
  const parentOf = new Map<string, string>();
  for (const span of spans) {
    const parent = verifiedParentOf(span, ids);
    if (!parent) continue;
    if (wouldCreateCycle(parentOf, span.span_id, parent)) continue;
    parentOf.set(span.span_id, parent);
  }
  return parentOf;
}

function wouldCreateCycle(parentOf: Map<string, string>, child: string, parent: string): boolean {
  let current: string | undefined = parent;
  const seen = new Set<string>();
  while (current) {
    if (current === child) return true;
    if (seen.has(current)) return true;
    seen.add(current);
    current = parentOf.get(current);
  }
  return false;
}

export function childrenMap(spans: Span[], parents: Map<string, string>): Map<string | null, Span[]> {
  const children = new Map<string | null, Span[]>();
  for (const span of spans) {
    const parent = parents.get(span.span_id) ?? null;
    const list = children.get(parent) ?? [];
    list.push(span);
    children.set(parent, list);
  }
  for (const list of children.values()) list.sort((a, b) => spanSortKey(a).localeCompare(spanSortKey(b)));
  return children;
}

export function classifyRelationships(spans: Span[]): GraphEdge[] {
  const clean = sanitizeSpans(spans);
  const parents = parentMap(clean);
  const children = childrenMap(clean, parents);
  const edges: GraphEdge[] = [];
  for (const [child, parent] of parents) {
    edges.push({ id: `parent:${parent}->${child}`, source: parent, target: child, kind: "parent" });
  }
  for (const siblings of children.values()) {
    for (let i = 1; i < siblings.length; i += 1) {
      const source = siblings[i - 1].span_id;
      const target = siblings[i].span_id;
      edges.push({ id: `order:${source}->${target}`, source, target, kind: "order" });
    }
  }
  return edges;
}

function groupFindings(memberIds: string[], findings: DiagnosticFinding[]) {
  const related = new Set<string>();
  let worst: FindingSeverity | null = null;
  for (const id of memberIds) {
    for (const finding of findingsForSpan(findings, id)) {
      related.add(finding.finding_id);
      if (!worst || SEVERITY_RANK[finding.severity] > SEVERITY_RANK[worst]) worst = finding.severity;
    }
  }
  return { findingCount: related.size, worstFindingSeverity: worst };
}

function makeUnsignedGroup(
  parentId: string | null,
  type: SpanType,
  name: string,
  members: Span[],
  findings: DiagnosticFinding[],
): Omit<SpanGroup, "id"> {
  const statuses = { ...STATUS_ZERO };
  let durationMs = 0;
  let failedCount = 0;
  const memberIds = members.map((span) => span.span_id);
  for (const span of members) {
    statuses[span.status] += 1;
    durationMs += span.duration_ms || 0;
    if (span.status === "error") failedCount += 1;
  }
  const { findingCount, worstFindingSeverity } = groupFindings(memberIds, findings);
  return {
    parentId,
    type,
    name,
    memberIds,
    durationMs,
    durationIsWallClock: false,
    statuses,
    failedCount,
    findingCount,
    worstFindingSeverity,
  };
}

export function seedGroupId(group: Omit<SpanGroup, "id"> | SpanGroup): string {
  return `group:${group.parentId ?? "root"}:${group.type}:${group.name}:${group.memberIds[0]}`;
}

function jaccard(a: string[], b: string[]): number {
  const left = new Set(a);
  const right = new Set(b);
  let inter = 0;
  for (const id of left) if (right.has(id)) inter += 1;
  const union = left.size + right.size - inter;
  return union === 0 ? 0 : inter / union;
}

export function assignStableGroupIds(
  unsigned: Array<Omit<SpanGroup, "id">>,
  previousGroups: SpanGroup[] = [],
): SpanGroup[] {
  const pairs: { nextIndex: number; prevIndex: number; score: number; prevId: string }[] = [];
  unsigned.forEach((group, nextIndex) => {
    previousGroups.forEach((prev, prevIndex) => {
      if (prev.parentId !== group.parentId || prev.type !== group.type || prev.name !== group.name) return;
      const score = jaccard(prev.memberIds, group.memberIds);
      if (score <= 0) return;
      pairs.push({ nextIndex, prevIndex, score, prevId: prev.id });
    });
  });
  pairs.sort((a, b) => b.score - a.score || a.prevId.localeCompare(b.prevId) || a.nextIndex - b.nextIndex);
  const assigned = new Array<string | null>(unsigned.length).fill(null);
  const usedPrev = new Set<number>();
  const usedIds = new Set<string>();
  for (const pair of pairs) {
    if (assigned[pair.nextIndex] || usedPrev.has(pair.prevIndex) || usedIds.has(pair.prevId)) continue;
    assigned[pair.nextIndex] = pair.prevId;
    usedPrev.add(pair.prevIndex);
    usedIds.add(pair.prevId);
  }
  return unsigned.map((group, index) => {
    let id = assigned[index];
    if (!id) {
      id = seedGroupId(group);
      let suffix = 1;
      while (usedIds.has(id)) {
        id = `${seedGroupId(group)}:${suffix}`;
        suffix += 1;
      }
    }
    usedIds.add(id);
    return { ...group, id };
  });
}

export function consecutiveToolGroups(
  spans: Span[],
  findings: DiagnosticFinding[] = [],
  previousGroups: SpanGroup[] = [],
): SpanGroup[] {
  const clean = sanitizeSpans(spans);
  const parents = parentMap(clean);
  const children = childrenMap(clean, parents);
  const unsigned: Array<Omit<SpanGroup, "id">> = [];
  for (const [parentId, siblings] of children) {
    let run: Span[] = [];
    const flush = () => {
      if (run.length >= 2) unsigned.push(makeUnsignedGroup(parentId, run[0].type, run[0].name, run, findings));
      run = [];
    };
    for (const span of siblings) {
      const canGroup = span.type === "tool";
      if (canGroup && run.length > 0 && run[0].type === span.type && run[0].name === span.name) {
        run.push(span);
        continue;
      }
      flush();
      if (canGroup) run = [span];
    }
    flush();
  }
  return assignStableGroupIds(unsigned, previousGroups);
}

export function remapExpandedGroups(previous: SpanGroup[], next: SpanGroup[], expanded: Iterable<string>): Set<string> {
  const prevById = new Map(previous.map((group) => [group.id, group]));
  const result = new Set<string>();
  for (const id of expanded) {
    const old = prevById.get(id);
    if (old) {
      const members = new Set(old.memberIds);
      for (const group of next) {
        if (group.id === id || group.memberIds.some((member) => members.has(member))) result.add(group.id);
      }
      continue;
    }
    if (next.some((group) => group.id === id)) result.add(id);
  }
  return result;
}

export function remapSelection(
  selectedId: string | null,
  previousGroups: SpanGroup[],
  nextGroups: SpanGroup[],
  spanIds: Set<string>,
): string | null {
  if (!selectedId) return null;
  if (spanIds.has(selectedId)) return selectedId;
  if (nextGroups.some((group) => group.id === selectedId)) return selectedId;
  const old = previousGroups.find((group) => group.id === selectedId);
  if (!old) return null;
  const members = new Set(old.memberIds);
  const overlapping = nextGroups.filter((group) => group.memberIds.some((id) => members.has(id)));
  if (overlapping.length === 0) return null;
  overlapping.sort((a, b) => b.memberIds.length - a.memberIds.length || a.id.localeCompare(b.id));
  const failedMember = old.memberIds.find((id) => overlapping.some((group) => group.memberIds.includes(id)));
  const preferred = overlapping.find((group) => failedMember && group.memberIds.includes(failedMember));
  return preferred?.id ?? overlapping[0].id;
}

export function ancestorIds(spanId: string, parents: Map<string, string>): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  let current: string | undefined = parents.get(spanId);
  while (current && !seen.has(current)) {
    seen.add(current);
    result.push(current);
    current = parents.get(current);
  }
  return result;
}

export function failureNeighborhood(
  spans: Span[],
  findings: DiagnosticFinding[] = [],
  selectedFindingId: string | null = null,
  radius = FAILURE_SIBLING_RADIUS,
): Set<string> {
  const clean = sanitizeSpans(spans);
  const parents = parentMap(clean);
  const children = childrenMap(clean, parents);
  const byId = new Map(clean.map((span) => [span.span_id, span]));
  const seeds = new Set<string>();
  for (const span of clean) if (span.status === "error") seeds.add(span.span_id);
  const selected = selectedFindingId ? findings.find((finding) => finding.finding_id === selectedFindingId) : null;
  const evidence = selected ? selected.span_ids : findings.flatMap((finding) => finding.span_ids);
  for (const id of evidence) if (byId.has(id)) seeds.add(id);

  const keep = new Set<string>();
  for (const seed of seeds) {
    keep.add(seed);
    for (const ancestor of ancestorIds(seed, parents)) keep.add(ancestor);
    for (const child of children.get(seed) ?? []) keep.add(child.span_id);
    const parent = parents.get(seed) ?? null;
    const siblings = children.get(parent) ?? [];
    const index = siblings.findIndex((span) => span.span_id === seed);
    if (index >= 0) {
      const start = Math.max(0, index - radius);
      const end = Math.min(siblings.length, index + radius + 1);
      for (let i = start; i < end; i += 1) keep.add(siblings[i].span_id);
    }
  }
  return keep;
}

function visibleIdForSpan(spanId: string, collapsed: Map<string, SpanGroup>, collapse: boolean): string {
  const group = collapsed.get(spanId);
  if (group && collapse) return group.id;
  return spanId;
}

export function buildVisibleGraph(options: VisibleGraphOptions): VisibleGraph {
  const findings = options.findings ?? [];
  const clean = sanitizeSpans(options.spans);
  const spanById = new Map(clean.map((span) => [span.span_id, span]));
  const groups = consecutiveToolGroups(clean, findings, options.previousGroups ?? []);
  const expanded = new Set(options.expandedGroupIds ?? []);
  const collapsedMembers = new Map<string, SpanGroup>();
  for (const group of groups) {
    if (expanded.has(group.id)) continue;
    for (const id of group.memberIds) collapsedMembers.set(id, group);
  }

  const allowed = options.mode === "failure" ? failureNeighborhood(clean, findings, options.selectedFindingId ?? null) : null;
  const nodes: VisibleNode[] = [];
  const emitted = new Set<string>();

  const includeSpan = (span: Span) => {
    if (allowed && !allowed.has(span.span_id)) return false;
    if (options.mode === "overview" && collapsedMembers.has(span.span_id)) return false;
    return true;
  };

  for (const span of clean) {
    if (!includeSpan(span)) continue;
    emitted.add(span.span_id);
    nodes.push({ id: span.span_id, kind: "span", span, group: null, sortKey: spanSortKey(span) });
  }

  if (options.mode === "overview") {
    for (const group of groups) {
      const members = group.memberIds.filter((id) => !allowed || allowed.has(id));
      if (members.length === 0 || expanded.has(group.id) || emitted.has(group.id)) continue;
      emitted.add(group.id);
      const first = spanById.get(group.memberIds[0]);
      nodes.push({
        id: group.id,
        kind: "group",
        span: null,
        group,
        sortKey: first ? spanSortKey(first) : group.id,
      });
    }
  }

  nodes.sort((a, b) => a.sortKey.localeCompare(b.sortKey) || a.id.localeCompare(b.id));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const collapse = options.mode === "overview";
  const edges: GraphEdge[] = [];
  const seenEdge = new Set<string>();

  for (const edge of classifyRelationships(clean)) {
    if (allowed) {
      const sourceGroup = collapsedMembers.get(edge.source);
      const targetGroup = collapsedMembers.get(edge.target);
      const sourceVisible = allowed.has(edge.source) || Boolean(sourceGroup?.memberIds.some((id) => allowed.has(id)));
      const targetVisible = allowed.has(edge.target) || Boolean(targetGroup?.memberIds.some((id) => allowed.has(id)));
      if (!sourceVisible || !targetVisible) continue;
    }
    const source = visibleIdForSpan(edge.source, collapsedMembers, collapse);
    const target = visibleIdForSpan(edge.target, collapsedMembers, collapse);
    if (source === target || !nodeIds.has(source) || !nodeIds.has(target)) continue;
    const mapped: GraphEdge = { id: `${edge.kind}:${source}->${target}`, source, target, kind: edge.kind };
    if (seenEdge.has(mapped.id)) continue;
    seenEdge.add(mapped.id);
    edges.push(mapped);
  }

  const treeParents = new Map<string, string>();
  for (const edge of edges) {
    if (edge.kind !== "parent") continue;
    if (wouldCreateCycle(treeParents, edge.target, edge.source)) continue;
    treeParents.set(edge.target, edge.source);
  }

  return {
    nodes,
    edges,
    groups,
    layout: layoutGraph(
      nodes.map((node) => node.id),
      treeParents,
    ),
    spanById,
  };
}

export function groupHighlightClass(
  group: SpanGroup,
  findings: DiagnosticFinding[],
  selectedFindingId: string | null,
): string {
  const classes: string[] = [];
  if (group.failedCount > 0) classes.push("is-error");
  const selected = selectedFindingId
    ? findings.find((finding) => finding.finding_id === selectedFindingId)
    : null;
  if (selected && selected.span_ids.some((id) => group.memberIds.includes(id))) {
    classes.push("is-finding-selected");
  } else if (group.findingCount > 0 && group.failedCount === 0) {
    if (group.worstFindingSeverity === "critical") classes.push("is-error");
    else if (group.worstFindingSeverity === "warning") classes.push("is-warning");
    else classes.push("is-info");
  }
  return classes.join(" ");
}

export function shouldFitGraph(reason: "initial" | "mode-change" | "finding-focus" | "poll"): boolean {
  return reason !== "poll";
}

export function groupsToExpandForFinding(groups: SpanGroup[], finding: DiagnosticFinding | null): string[] {
  if (!finding) return [];
  return groups.filter((group) => group.memberIds.some((id) => finding.span_ids.includes(id))).map((group) => group.id);
}

export function sameIdSet(a: Iterable<string>, b: Iterable<string>): boolean {
  const left = new Set(a);
  const right = new Set(b);
  if (left.size !== right.size) return false;
  for (const id of left) if (!right.has(id)) return false;
  return true;
}

export function groupMembershipKey(groups: SpanGroup[]): string {
  return groups.map((group) => `${group.id}=${group.memberIds.join(",")}`).join("|");
}
