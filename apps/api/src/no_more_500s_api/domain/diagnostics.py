from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DetectionType(StrEnum):
    TOOL_FAILURE = "tool_failure"
    REPEATED_TOOL_CALL = "repeated_tool_call"
    SCHEMA_MISMATCH = "schema_mismatch"
    INEFFECTIVE_RECOVERY = "ineffective_recovery"
    STALL = "stall"
    INTERRUPTION = "interruption"
    PROVIDER_ERROR = "provider_error"
    LATENCY_ANOMALY = "latency_anomaly"
    TOKEN_ANOMALY = "token_anomaly"
    COST_ANOMALY = "cost_anomaly"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class FindingConfidence(StrEnum):
    CONFIRMED = "confirmed"
    HEURISTIC = "heuristic"


class AnalysisStatus(StrEnum):
    FINALIZED = "finalized"
    PROVISIONAL = "provisional"


class DiagnosticFinding(BaseModel):
    finding_id: UUID
    trace_id: UUID
    span_ids: list[UUID] = Field(default_factory=list)
    detector_id: str
    detection_type: DetectionType
    severity: FindingSeverity
    confidence: FindingConfidence
    title: str
    explanation: str
    evidence: list[str] = Field(default_factory=list)
    evidence_data: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    detector_version: str
    provisional: bool = False


class InstrumentationCoverage(BaseModel):
    token_status: str
    cost_status: str
    instrumentation_coverage: str | None = None
    unsupported_detectors: list[str] = Field(default_factory=list)
    insufficient_data_detectors: list[str] = Field(default_factory=list)


class TraceDiagnostics(BaseModel):
    trace_id: UUID
    analysis_status: AnalysisStatus
    coverage: InstrumentationCoverage
    findings: list[DiagnosticFinding] = Field(default_factory=list)


class DiagnosticsAnalysisScope(BaseModel):
    limit: int
    offset: int = 0
    project_id: str | None = None
    analyzed_run_count: int
    newest_started_at: datetime | None = None
    oldest_started_at: datetime | None = None
    note: str


class DiagnosticSummary(BaseModel):
    analysis_scope: DiagnosticsAnalysisScope
    totals_by_severity: dict[str, int]
    totals_by_type: dict[str, int]
    affected_trace_count: int
    recent_findings: list[DiagnosticFinding]
    coverage_notes: list[str] = Field(default_factory=list)
