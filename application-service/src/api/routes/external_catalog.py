"""Tenant-facing external catalog routes (gateway rewrites /api → /internal/v1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from ...api.dependencies import Principal, get_principal, get_runtime, get_sync_db
from ...api.schemas.common import success
from ...runtime import ProcessRuntime
from ...schemas.external_catalog_schema import InstallRequest, PublicationRequest
from ...services.external_catalog_service import ExternalCatalogService

router = APIRouter(prefix="/external-catalog", tags=["external-catalog"])
DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
RuntimeDep = Annotated[ProcessRuntime, Depends(get_runtime)]


def _service(db: Session, runtime: ProcessRuntime) -> ExternalCatalogService:
    return ExternalCatalogService(
        db=db,
        gateway=runtime.market_gateway,
        settings=runtime.settings,
        idempotency=_idempotency(runtime),
    )


def _idempotency(runtime: ProcessRuntime):
    from ...external_catalog.idempotency import IdempotencyStore

    return IdempotencyStore(runtime.redis.sync_client())


@router.post("/publications", summary="投稿到社区市场")
def create_publication(
    payload: PublicationRequest,
    db: DbSession,
    runtime: RuntimeDep,
    caller: Caller,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    service = _service(db, runtime)
    data = service.publish(
        caller.tenant_id, caller.actor_id, payload, idempotency_key
    )
    return success(data=data.model_dump(mode="json"))


@router.post(
    "/publications/{publication_id}/withdraw", summary="撤回投稿"
)
def withdraw_publication(
    publication_id: str,
    db: DbSession,
    runtime: RuntimeDep,
    caller: Caller,
):
    service = _service(db, runtime)
    data = service.withdraw(caller.tenant_id, caller.actor_id, publication_id)
    return success(data=data.model_dump(mode="json"))


@router.get("/publications", summary="查询本地资源投稿状态")
def get_publication_status(
    db: DbSession,
    runtime: RuntimeDep,
    caller: Caller,
    resource_type: Annotated[str, Query(...)],
    resource_id: Annotated[str, Query(...)],
):
    service = _service(db, runtime)
    data = service.publication_status(
        caller.tenant_id, resource_type, resource_id
    )
    return success(data=data.model_dump(mode="json"))


@router.post("/installations", summary="从社区市场安装")
def create_installation(
    payload: InstallRequest,
    db: DbSession,
    runtime: RuntimeDep,
    caller: Caller,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    service = _service(db, runtime)
    data = service.install(
        caller.tenant_id, caller.actor_id, payload, idempotency_key
    )
    return success(data=data.model_dump(mode="json"))


@router.delete(
    "/installations/{resource_type}/{local_resource_id}", summary="卸载市场安装"
)
def delete_installation(
    resource_type: str,
    local_resource_id: str,
    db: DbSession,
    runtime: RuntimeDep,
    caller: Caller,
):
    service = _service(db, runtime)
    data = service.uninstall(caller.tenant_id, resource_type, local_resource_id)
    return success(data=data.model_dump(mode="json"))
