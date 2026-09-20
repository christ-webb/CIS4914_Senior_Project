from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from no_more_500s_api.domain.models import Trace, TraceCreate, TraceSummary
from no_more_500s_api.services.trace_service import TraceNotFoundError, trace_repository

router = APIRouter(prefix="/v1/traces", tags=["traces"])


@router.post("", response_model=Trace, status_code=status.HTTP_201_CREATED)
def create_trace(payload: TraceCreate) -> Trace:
    return trace_repository.save(payload)


@router.get("", response_model=list[TraceSummary])
def list_traces() -> list[TraceSummary]:
    return list(trace_repository.list())


@router.get("/{trace_id}", response_model=Trace)
def get_trace(trace_id: UUID) -> Trace:
    try:
        return trace_repository.get(trace_id)
    except TraceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Trace not found") from error

