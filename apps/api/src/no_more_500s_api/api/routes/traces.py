from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from no_more_500s_api.domain.models import Trace, TraceCreate, TraceSummary
from no_more_500s_api.services.failure_detection import LoopFinding, detect_repeated_tool_calls
from no_more_500s_api.services.trace_service import TraceNotFoundError, TraceRepository

router = APIRouter(prefix="/v1/traces", tags=["traces"])


def get_repository(request: Request) -> TraceRepository:
    return request.app.state.trace_repository  # type: ignore[no-any-return]


RepositoryDependency = Annotated[TraceRepository, Depends(get_repository)]


@router.post("", response_model=Trace, status_code=status.HTTP_201_CREATED)
def create_trace(payload: TraceCreate, trace_repository: RepositoryDependency) -> Trace:
    return trace_repository.save(payload)


@router.get("", response_model=list[TraceSummary])
def list_traces(trace_repository: RepositoryDependency) -> list[TraceSummary]:
    return list(trace_repository.list())


@router.get("/{trace_id}", response_model=Trace)
def get_trace(trace_id: UUID, trace_repository: RepositoryDependency) -> Trace:
    try:
        return trace_repository.get(trace_id)
    except TraceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Trace not found") from error


@router.get("/{trace_id}/failures", response_model=list[LoopFinding])
def get_trace_failures(trace_id: UUID, trace_repository: RepositoryDependency) -> list[LoopFinding]:
    """Analyze a stored trace for identical repeated tool calls.

    This read-only endpoint returns heuristic findings; an empty list means no
    repeated-tool-call pattern was found, not that the trace is error-free.
    """
    try:
        trace = trace_repository.get(trace_id)
    except TraceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Trace not found") from error
    return detect_repeated_tool_calls(trace)
