import { useCallback, useMemo, useState } from "react";

import {
  buildVisibleGraph,
  consecutiveToolGroups,
  groupMembershipKey,
  remapExpandedGroups,
  remapSelection,
  sameIdSet,
  type GraphMode,
  type SpanGroup,
  type VisibleGraph,
} from "@/lib/execution-graph";
import type { DiagnosticFinding, Span } from "@/lib/types";

export function useVisibleGraph({
  spans,
  mode,
  findings = [],
  selectedFindingId = null,
  activeId,
  onSelect,
}: {
  spans: Span[];
  mode: GraphMode;
  findings?: DiagnosticFinding[];
  selectedFindingId?: string | null;
  activeId?: string | null;
  onSelect?: (id: string | null) => void;
}): {
  graph: VisibleGraph;
  expanded: Set<string>;
  toggleGroup: (id: string) => void;
  expandGroups: (ids: Iterable<string>) => void;
} {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [stickyGroups, setStickyGroups] = useState<SpanGroup[]>([]);
  const [seenSpans, setSeenSpans] = useState(spans);
  const spanIds = useMemo(() => new Set(spans.map((span) => span.span_id)), [spans]);

  if (spans !== seenSpans) {
    const nextGroups = consecutiveToolGroups(spans, findings, stickyGroups);
    const nextExpanded = remapExpandedGroups(stickyGroups, nextGroups, expanded);
    const nextActive = remapSelection(activeId ?? null, stickyGroups, nextGroups, spanIds);
    setSeenSpans(spans);
    setStickyGroups(nextGroups);
    if (!sameIdSet(expanded, nextExpanded)) setExpanded(nextExpanded);
    if (onSelect && nextActive !== (activeId ?? null)) onSelect(nextActive);
  }

  const graph = useMemo(
    () =>
      buildVisibleGraph({
        spans,
        mode,
        expandedGroupIds: expanded,
        previousGroups: stickyGroups,
        findings,
        selectedFindingId,
      }),
    [spans, mode, expanded, stickyGroups, findings, selectedFindingId],
  );

  if (groupMembershipKey(graph.groups) !== groupMembershipKey(stickyGroups)) {
    setStickyGroups(graph.groups);
  }

  const toggleGroup = useCallback((id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandGroups = useCallback((ids: Iterable<string>) => {
    setExpanded((current) => new Set([...current, ...ids]));
  }, []);

  return { graph, expanded, toggleGroup, expandGroups };
}
