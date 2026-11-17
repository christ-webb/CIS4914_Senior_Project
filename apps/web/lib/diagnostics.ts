import type { DiagnosticFinding, FindingSeverity } from "./types.ts";

const SEVERITY_RANK: Record<FindingSeverity, number> = { critical: 3, warning: 2, info: 1 };

export function findingsForSpan(findings: DiagnosticFinding[], spanId: string): DiagnosticFinding[] {
  return findings.filter((finding) => finding.span_ids.includes(spanId));
}

export function spanHighlightClass(
  spanId: string,
  status: string,
  findings: DiagnosticFinding[],
  selectedFindingId: string | null,
): string {
  const related = findingsForSpan(findings, spanId);
  const classes: string[] = [];
  if (status === "error") classes.push("is-error");
  if (selectedFindingId && related.some((finding) => finding.finding_id === selectedFindingId)) {
    classes.push("is-finding-selected");
  }
  if (related.length > 0 && status !== "error") {
    const worst = related.reduce((max, finding) => (SEVERITY_RANK[finding.severity] > SEVERITY_RANK[max] ? finding.severity : max), "info" as FindingSeverity);
    if (worst === "critical") classes.push("is-error");
    else if (worst === "warning") classes.push("is-warning");
    else classes.push("is-info");
  }
  return classes.join(" ");
}

export function sortFindings(findings: DiagnosticFinding[]): DiagnosticFinding[] {
  return [...findings].sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a.title.localeCompare(b.title));
}
