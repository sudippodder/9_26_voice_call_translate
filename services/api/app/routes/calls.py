"""Call management routes — POST /calls, GET/accept/reject/end."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import get_current_user
from app.models.schemas import (
    CallAcceptResponse,
    CallCreate,
    CallCreateResponse,
    CallListResponse,
    CallOut,
    UserOut,
)
from app.redis import rate_limit
from app.services.calls import (
    CallNotFoundError,
    CallValidationError,
    accept_call,
    create_call,
    end_call,
    get_call,
    list_calls,
    reject_call,
)

router = APIRouter()


@router.post("/api/v1/calls", response_model=CallCreateResponse, status_code=status.HTTP_201_CREATED)
async def post_call(
    payload: CallCreate,
    user: UserOut = Depends(get_current_user),
) -> CallCreateResponse:
    if not await rate_limit(user.id, "create_call", limit=20, window=60):
        raise HTTPException(status_code=429, detail="Too many call requests")
    try:
        return await create_call(caller_id=user.id, payload=payload)
    except CallValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/v1/calls/{call_id}", response_model=CallOut)
async def get_call_route(call_id: str, user: UserOut = Depends(get_current_user)) -> CallOut:
    try:
        return await get_call(call_id, user.id)
    except CallNotFoundError as e:
        raise HTTPException(status_code=404, detail="Call not found") from e
    except CallValidationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.post("/api/v1/calls/{call_id}/accept", response_model=CallAcceptResponse)
async def accept_call_route(call_id: str, user: UserOut = Depends(get_current_user)) -> CallAcceptResponse:
    try:
        return await accept_call(call_id, user.id)
    except CallNotFoundError as e:
        raise HTTPException(status_code=404, detail="Call not found") from e
    except CallValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/v1/calls/{call_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def reject_call_route(call_id: str, user: UserOut = Depends(get_current_user)) -> None:
    try:
        await reject_call(call_id, user.id)
    except CallNotFoundError as e:
        raise HTTPException(status_code=404, detail="Call not found") from e
    except CallValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/v1/calls/{call_id}/end",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def end_call_route(call_id: str, user: UserOut = Depends(get_current_user)) -> None:
    try:
        await end_call(call_id, user.id)
    except CallNotFoundError as e:
        raise HTTPException(status_code=404, detail="Call not found") from e
    except CallValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/v1/calls", response_model=CallListResponse)
async def list_calls_route(user: UserOut = Depends(get_current_user)) -> CallListResponse:
    calls = await list_calls(user.id)
    return CallListResponse(calls=calls)
