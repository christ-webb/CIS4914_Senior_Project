from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from no_more_500s_api.domain.models import SpanStatus, Trace, TraceCreate, TraceSummary
from no_more_500s_api.services.failure_detection import LoopFinding, detect_repeated_tool_calls
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


@router.post("/traces", response_model=Trace, status_code=status.HTTP_201_CREATED)
def create_trace(payload: TraceCreate, trace_repository: RepositoryDependency) -> Trace:
    try:
        return trace_repository.save(payload)
    except TraceConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trace already exists",
        ) from error
    except TraceStorageError as error:
        raise _storage_http_error() from error


@router.get("/traces", response_model=list[TraceSummary])
def list_traces(
    trace_repository: RepositoryDependency,
    project_id: str | None = None,
    status_filter: Annotated[SpanStatus | None, Query(alias="status")] = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TraceSummary]:
    query = TraceListQuery(
        project_id=project_id,
        status=status_filter,
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
    """
    try:
        trace = trace_repository.get(trace_id)
    except TraceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Trace not found") from error
    except TraceStorageError as error:
        raise _storage_http_error() from error
    return detect_repeated_tool_calls(trace)
