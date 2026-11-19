from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from no_more_500s_api.core.config import get_settings
from no_more_500s_api.domain.diagnostics import (
    DetectionType,
    DiagnosticSummary,
    FindingConfidence,
    FindingSeverity,
    TraceDiagnostics,
)
from no_more_500s_api.domain.models import (
    RunEventIn,
    RunFinalize,
    RunLifecycle,
    RunStart,
    SpanStatus,
    Trace,
    TraceCreate,
    TraceSummary,
)
from no_more_500s_api.services.diagnostics import (
    analyze_trace,
    context_from_settings,
    filter_findings,
    summarize_traces,
)
from no_more_500s_api.services.failure_detection import LoopFinding, detect_repeated_tool_calls
from no_more_500s_api.services.run_reconstruction import InvalidLifecycleError
from no_more_500s_api.services.trace_service import (
    TraceConflictError,
    TraceListQuery,
    TraceNotFoundError,
    TraceRepository,
    TraceStorageError,
)

router = APIRouter(prefix="/v1", tags=["traces"])


def get_repository(request: Request) -> TraceRepository:
    return request.app.state.trace_repository  # type: ignore[no-any-return]


RepositoryDependency = Annotated[TraceRepository, Depends(get_repository)]


def _storage_http_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Trace storage is unavailable",
    )


def _translate_write_errors(error: Exception) -> HTTPException:
    if isinstance(error, TraceConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Trace already exists")
    if isinstance(error, InvalidLifecycleError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invalid lifecycle transition",
        )
    if isinstance(error, TraceNotFoundError):
        return HTTPException(status_code=404, detail="Trace not found")
    if isinstance(error, TraceStorageError):
        return _storage_http_error()
    raise error


@router.post("/traces", response_model=Trace, status_code=status.HTTP_201_CREATED)
def create_trace(payload: TraceCreate, trace_repository: RepositoryDependency) -> Trace:
    try:
        return trace_repository.save(payload)
    except (TraceConflictError, TraceStorageError) as error:
        raise _translate_write_errors(error) from error


@router.post("/runs", response_model=Trace, status_code=status.HTTP_201_CREATED)
def start_run(payload: RunStart, trace_repository: RepositoryDependency) -> Trace:
    try:
        return trace_repository.start_run(payload)
    except (TraceConflictError, TraceStorageError, InvalidLifecycleError) as error:
        raise _translate_write_errors(error) from error


@router.post("/runs/{trace_id}/events", response_model=Trace)
def append_run_events(
    trace_id: UUID,
    payload: list[RunEventIn],
    trace_repository: RepositoryDependency,
) -> Trace:
    try:
        return trace_repository.append_events(trace_id, payload)
    except (TraceNotFoundError, TraceStorageError, InvalidLifecycleError) as error:
        raise _translate_write_errors(error) from error


@router.post("/runs/{trace_id}/finalize", response_model=Trace)
def finalize_run(
    trace_id: UUID,
    payload: RunFinalize,
    trace_repository: RepositoryDependency,
) -> Trace:
    try:
        return trace_repository.finalize_run(trace_id, payload)
    except (TraceNotFoundError, TraceStorageError, InvalidLifecycleError) as error:
        raise _translate_write_errors(error) from error


@router.get("/traces", response_model=list[TraceSummary])
def list_traces(
    trace_repository: RepositoryDependency,
    fixtures: Literal["exclude", "only", "include"] = "include",
    activity: Literal["active", "idle", "terminal", "completed_turns"] | None = None,
    project_id: str | None = None,
    status_filter: Annotated[SpanStatus | None, Query(alias="status")] = None,
    lifecycle: RunLifecycle | None = None,
    source: str | None = None,
    provider: str | None = None,
    agent_framework: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TraceSummary]:
    query = TraceListQuery(
        fixtures=fixtures,
        activity=activity,
        project_id=project_id,
        status=status_filter,
        lifecycle=lifecycle,
        source=source,
        provider=provider,
        agent_framework=agent_framework,
        started_after=started_after,
        started_before=started_before,
        limit=limit,
        offset=offset,
    )
    try:
        return list(trace_repository.list(query))
    except TraceStorageError as error:
        raise _storage_http_error() from error


@router.get("/projects", response_model=list[str])
def list_projects(trace_repository: RepositoryDependency) -> list[str]:
    try:
        return list(trace_repository.list_projects())
    except TraceStorageError as error:
        raise _storage_http_error() from error


@router.get("/traces/{trace_id}", response_model=Trace)
def get_trace(trace_id: UUID, trace_repository: RepositoryDependency) -> Trace:
    try:
        return trace_repository.get(trace_id)
    except TraceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Trace not found") from error
    except TraceStorageError as error:
        raise _storage_http_error() from error


@router.get("/traces/{trace_id}/failures", response_model=list[LoopFinding])
def get_trace_failures(trace_id: UUID, trace_repository: RepositoryDependency) -> list[LoopFinding]:
    """Analyze a stored trace for identical repeated tool calls.

    This read-only endpoint returns heuristic findings; an empty list means no
    repeated-tool-call pattern was found, not that the trace is error-free.
    Findings on running traces are provisional.
    """
    try:
        trace = trace_repository.get(trace_id)
    except TraceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Trace not found") from error
    except TraceStorageError as error:
        raise _storage_http_error() from error
    return detect_repeated_tool_calls(trace)


def _load_traces(
    repository: TraceRepository,
    query: TraceListQuery,
) -> list[Trace]:
    summaries = list(repository.list(query))
    traces: list[Trace] = []
    for summary in summaries:
        try:
            traces.append(repository.get(summary.trace_id))
        except TraceNotFoundError:
            continue
    return traces


def _bounded_limit(requested: int | None) -> int:
    settings = get_settings()
    default = max(1, min(50, settings.diagnostics_summary_limit))
    if requested is None:
        return default
    return max(1, min(50, requested))


@router.get("/diagnostics", response_model=DiagnosticSummary)
def get_diagnostic_summary(
    trace_repository: RepositoryDependency,
    fixtures: Literal["exclude", "only", "include"] = "include",
    project_id: str | None = None,
    status_filter: Annotated[SpanStatus | None, Query(alias="status")] = None,
    lifecycle: RunLifecycle | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DiagnosticSummary:
    """Analyze a bounded recent window of traces. Counts are not all-history."""
    window = _bounded_limit(limit)
    query = TraceListQuery(
        fixtures=fixtures,
        project_id=project_id,
        status=status_filter,
        lifecycle=lifecycle,
        started_after=started_after,
        started_before=started_before,
        limit=window,
        offset=offset,
    )
    try:
        traces = _load_traces(trace_repository, query)
    except TraceStorageError as error:
        raise _storage_http_error() from error
    ctx = context_from_settings(peers=traces)
    return summarize_traces(
        traces,
        ctx=ctx,
        limit=window,
        offset=offset,
        project_id=project_id,
    )


@router.get("/traces/{trace_id}/diagnostics", response_model=TraceDiagnostics)
def get_trace_diagnostics(
    trace_id: UUID,
    trace_repository: RepositoryDependency,
    severity: FindingSeverity | None = None,
    detection_type: DetectionType | None = None,
    confidence: FindingConfidence | None = None,
    span_id: UUID | None = None,
) -> TraceDiagnostics:
    try:
        trace = trace_repository.get(trace_id)
        peer_limit = _bounded_limit(None)
        peers = _load_traces(
            trace_repository,
            TraceListQuery(
                project_id=trace.project_id,
                limit=peer_limit,
                fixtures="only" if trace.attributes.get("nm5.is_fixture") is True else "exclude",
            ),
        )
    except TraceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Trace not found") from error
    except TraceStorageError as error:
        raise _storage_http_error() from error
    diagnostics = analyze_trace(trace, context_from_settings(peers=peers))
    return filter_findings(
        diagnostics,
        severity=severity,
        detection_type=detection_type,
        confidence=confidence,
        span_id=str(span_id) if span_id else None,
    )
