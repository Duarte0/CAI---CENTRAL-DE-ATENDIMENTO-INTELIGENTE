"""Authenticated, read-only administrative identity projections."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime
from typing import Any, Literal, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.config import require_admin_api_token, settings
from src.core.identity_admin import (
    get_identity_contact_projection,
    list_active_company_projection,
    list_identity_link_projection,
)
from src.core.identity_resolution import (
    IdentityCommandConflictError,
    IdentityConflictError,
    IdentityResolutionError,
    confirm_identity_link_admin,
    reject_identity_link_admin,
)

logger = logging.getLogger(__name__)

IdentityState = Literal[
    "candidate", "confirmed", "rejected", "ambiguous", "unresolved", "conflict"
]
LinkState = Literal["candidate", "confirmed", "rejected"]
EVIDENCE_TYPES = {"exact_phone", "exact_email", "brazil_mobile_variant"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_ADMIN_AUTH_DETAIL = "Invalid administrative credentials"


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acessorias_company_external_id: str
    evidence_type: Literal["exact_phone", "exact_email", "brazil_mobile_variant"]
    count: int = Field(ge=1)
    latest_observed_at: datetime


class CompanyProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acessorias_company_external_id: str
    display_name: str
    is_present: bool
    is_active: bool | None
    available: bool


class IdentityLinkProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acessorias_company_external_id: str
    state: LinkState
    source: str
    confirmation_source: str | None
    confirmed_at: datetime | None
    rejection_reason: str | None
    display_name: str
    is_present: bool
    is_active: bool | None
    available: bool
    created_at: datetime
    updated_at: datetime


class IdentityTransitionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acessorias_company_external_id: str
    from_state: LinkState | None
    to_state: LinkState
    source: str
    reason: str
    confirmation_source: str | None
    confirmed_at: datetime | None
    created_at: datetime


class IdentityLinkListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    digisac_contact_external_id: str
    display_name: str | None
    is_group: bool | None
    state: IdentityState
    candidate_company_count: int = Field(ge=0)
    links: list[IdentityLinkProjection]
    evidence: list[EvidenceSummary]


class IdentityLinkListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IdentityLinkListItem]
    next_cursor: str | None


class IdentityContactDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    digisac_contact_external_id: str
    display_name: str | None
    is_group: bool | None
    state: IdentityState
    candidate_company_count: int = Field(ge=0)
    links: list[IdentityLinkProjection]
    evidence: list[EvidenceSummary]
    transitions: list[IdentityTransitionProjection]
    candidate_companies: list[CompanyProjection]


class CompanyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CompanyProjection]
    next_cursor: str | None


class IdentityCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_:-]{1,120}", normalized):
            raise ValueError("reason must be a safe nonblank category")
        return normalized

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if (
            value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("idempotency_key must be opaque and nonblank")
        return value


class IdentityLinkConfirmRequest(IdentityCommandRequest):
    acessorias_company_external_id: str = Field(min_length=1, max_length=200)

    @field_validator("acessorias_company_external_id")
    @classmethod
    def validate_company_external_id(cls, value: str) -> str:
        if (
            value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("acessorias_company_external_id must be opaque and nonblank")
        return value


class IdentityLinkRejectRequest(IdentityCommandRequest):
    pass


class IdentityLinkCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    digisac_contact_external_id: str
    acessorias_company_external_id: str
    state: LinkState
    source: str
    confirmation_source: str | None
    confirmed_at: datetime | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime


def _safe_request_id(request: Request) -> str:
    value = request.headers.get("X-Request-ID")
    return value if value and _REQUEST_ID.fullmatch(value) else "unavailable"


def _reject_admin_auth(request: Request, reason: str) -> NoReturn:
    logger.warning(
        "Administrative authentication rejected",
        extra={"reason": reason, "request_id": _safe_request_id(request)},
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_ADMIN_AUTH_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_admin_token(request: Request) -> None:
    configured = settings.admin_api_token
    if not configured:
        logger.error(
            "Administrative API token is unavailable",
            extra={"reason": "missing_admin_configuration", "request_id": _safe_request_id(request)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrative API unavailable",
        )
    authorization = request.headers.get("Authorization")
    if authorization is None:
        _reject_admin_auth(request, "missing_admin_token")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        _reject_admin_auth(request, "missing_admin_token")
    presented = parts[1]
    if not hmac.compare_digest(presented, configured):
        _reject_admin_auth(request, "invalid_admin_token")


def _cursor_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _encode_cursor(
    *,
    scope: str,
    parameters: dict[str, Any],
    after: tuple[str, int],
) -> str:
    payload = {"after": list(after), "parameters": parameters, "scope": scope, "version": 1}
    encoded = base64.urlsafe_b64encode(_cursor_bytes(payload)).rstrip(b"=")
    signature = hmac.new(
        require_admin_api_token().encode("utf-8"), encoded, hashlib.sha256
    ).hexdigest()
    return f"{encoded.decode('ascii')}.{signature}"


def _decode_cursor(
    value: str,
    *,
    scope: str,
    parameters: dict[str, Any],
) -> tuple[str, int]:
    try:
        encoded, signature = value.split(".", 1)
        if len(signature) != 64:
            raise ValueError
        expected = hmac.new(
            require_admin_api_token().encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict):
            raise ValueError
        payload = cast(dict[str, Any], payload)
        if set(payload) != {"after", "parameters", "scope", "version"}:
            raise ValueError
        if payload["version"] != 1 or payload["scope"] != scope:
            raise ValueError
        if payload["parameters"] != parameters:
            raise ValueError
        after_value = payload["after"]
        if not isinstance(after_value, list):
            raise ValueError
        after = cast(list[Any], after_value)
        if (
            len(after) != 2
            or not isinstance(after[0], str)
            or not after[0].strip()
            or not isinstance(after[1], int)
            or isinstance(after[1], bool)
            or after[1] <= 0
        ):
            raise ValueError
        return after[0], after[1]
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


def _validated_state(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in {
        "candidate", "confirmed", "rejected", "ambiguous", "unresolved", "conflict"
    }:
        raise HTTPException(status_code=400, detail="Invalid identity state")
    return normalized


def _validated_limit(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="limit must be an integer") from exc
    if str(parsed) != str(value).strip() or not 1 <= parsed <= 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return parsed


def _validated_query(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if len(normalized) > 120:
        raise HTTPException(status_code=400, detail="query is too long")
    return normalized or None


def _raise_identity_command_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, IdentityCommandConflictError):
        raise HTTPException(status_code=409, detail="Idempotency key conflict") from exc
    if isinstance(exc, IdentityConflictError):
        raise HTTPException(status_code=409, detail="Identity confirmation conflict") from exc
    if isinstance(exc, IdentityResolutionError):
        if exc.category == "directory_company_unavailable":
            raise HTTPException(status_code=422, detail="Acessórias company unavailable") from exc
        raise HTTPException(status_code=409, detail="Identity command could not be completed") from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail="Identity reference not found") from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail="Invalid administrative command body") from exc
    raise HTTPException(status_code=409, detail="Identity command could not be completed") from exc


admin_router = APIRouter(
    prefix="/admin/acessorias",
    tags=["Administração"],
    dependencies=[Depends(require_admin_token)],
)


@admin_router.get(
    "/identity-links",
    response_model=IdentityLinkListResponse,
    summary="List identity-link triage projections",
)
async def identity_link_list(
    state: str | None = None,
    cursor: str | None = None,
    limit: str = "50",
) -> IdentityLinkListResponse:
    selected_state = _validated_state(state)
    selected_limit = _validated_limit(limit)
    parameters = {"state": selected_state}
    after = (
        _decode_cursor(cursor, scope="identity-links", parameters=parameters)
        if cursor
        else None
    )
    projection = await list_identity_link_projection(
        state=selected_state, after=after, limit=selected_limit
    )
    next_after = projection["next_after"]
    next_cursor = (
        _encode_cursor(
            scope="identity-links", parameters=parameters, after=next_after
        )
        if next_after is not None
        else None
    )
    return IdentityLinkListResponse(items=projection["items"], next_cursor=next_cursor)


@admin_router.get(
    "/contacts/{digisac_contact_external_id}/identity",
    response_model=IdentityContactDetail,
    summary="Get one identity-link detail projection",
)
async def identity_contact_detail(
    digisac_contact_external_id: str,
) -> IdentityContactDetail:
    projection = await get_identity_contact_projection(digisac_contact_external_id)
    if projection is None:
        raise HTTPException(status_code=404, detail="DigiSac contact not found")
    return IdentityContactDetail.model_validate(projection)


@admin_router.get(
    "/companies",
    response_model=CompanyListResponse,
    summary="List active Acessórias companies",
)
async def active_company_list(
    query: str | None = None,
    cursor: str | None = None,
    limit: str = "50",
) -> CompanyListResponse:
    selected_query = _validated_query(query)
    selected_limit = _validated_limit(limit)
    parameters = {"query": selected_query}
    after = (
        _decode_cursor(cursor, scope="companies", parameters=parameters)
        if cursor
        else None
    )
    projection = await list_active_company_projection(
        query=selected_query, after=after, limit=selected_limit
    )
    next_after = projection["next_after"]
    next_cursor = (
        _encode_cursor(scope="companies", parameters=parameters, after=next_after)
        if next_after is not None
        else None
    )
    return CompanyListResponse(items=projection["items"], next_cursor=next_cursor)


@admin_router.post(
    "/contacts/{digisac_contact_external_id}/identity-links/confirm",
    response_model=IdentityLinkCommandResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm one identity link",
)
async def identity_link_confirm(
    digisac_contact_external_id: str,
    payload: IdentityLinkConfirmRequest,
    response: Response,
) -> IdentityLinkCommandResponse:
    try:
        command = await confirm_identity_link_admin(
            digisac_contact_external_id,
            payload.acessorias_company_external_id,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
        )
    except Exception as exc:
        _raise_identity_command_http_error(exc)
    response.status_code = 200 if command["replayed"] else status.HTTP_201_CREATED
    return IdentityLinkCommandResponse.model_validate(command["result"])


@admin_router.post(
    "/contacts/{digisac_contact_external_id}/identity-links/{acessorias_company_external_id}/reject",
    response_model=IdentityLinkCommandResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reject one identity link",
)
async def identity_link_reject(
    digisac_contact_external_id: str,
    acessorias_company_external_id: str,
    payload: IdentityLinkRejectRequest,
    response: Response,
) -> IdentityLinkCommandResponse:
    try:
        command = await reject_identity_link_admin(
            digisac_contact_external_id,
            acessorias_company_external_id,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
        )
    except Exception as exc:
        _raise_identity_command_http_error(exc)
    response.status_code = 200 if command["replayed"] else status.HTTP_201_CREATED
    return IdentityLinkCommandResponse.model_validate(command["result"])
