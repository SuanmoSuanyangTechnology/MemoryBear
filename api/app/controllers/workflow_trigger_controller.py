"""Workflow Trigger API."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.services.workflow_service import WorkflowService, get_workflow_service

router = APIRouter(prefix="/workflows/triggers", tags=["Workflow Trigger"])
logger = get_business_logger()


def _parse_custom_response_body(body: Any) -> Any:
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, str):
        text = body.strip()
        if not text:
            return ""
        try:
            import json
            return json.loads(text)
        except Exception:
            return body
    return body


async def _read_request_body(request: Request) -> Any:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            return await request.json()
        except Exception:
            return None

    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return dict(form)

    raw = await request.body()
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.hex()


@router.api_route("/webhook/{route_key}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def trigger_webhook(
    route_key: str,
    request: Request,
    workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)],
):
    logger.info(
        "收到工作流 Webhook 触发请求",
        extra={
            "route_key": route_key,
            "method": request.method.upper(),
            "client_ip": request.client.host if request.client else None,
            "url": str(request.url),
        },
    )
    matched = workflow_service.find_published_webhook_trigger(route_key)
    if not matched:
        logger.warning(
            "Webhook 触发失败: 未找到匹配的已发布触发器",
            extra={
                "route_key": route_key,
                "method": request.method.upper(),
                "client_ip": request.client.host if request.client else None,
            },
        )
        raise BusinessException("Webhook 触发器不存在", BizCode.NOT_FOUND)

    app, release, wf_config, trigger = matched
    trigger_config = trigger.get("config") or {}
    expected_method = str(trigger_config.get("method", "POST")).upper()
    if request.method.upper() != expected_method:
        logger.warning(
            "Webhook 触发失败: 请求方法不匹配",
            extra={
                "route_key": route_key,
                "app_id": str(app.id),
                "release_id": str(release.id),
                "expected_method": expected_method,
                "actual_method": request.method.upper(),
                "client_ip": request.client.host if request.client else None,
            },
        )
        raise BusinessException(f"Webhook 仅支持 {expected_method}", BizCode.INVALID_PARAMETER)

    event = {
        "body": await _read_request_body(request),
        "query": dict(request.query_params),
        "headers": dict(request.headers),
        "meta": {
            "method": request.method.upper(),
            "client": request.client.host if request.client else None,
            "url": str(request.url),
        },
    }
    result = await workflow_service.invoke_webhook_trigger(
        app=app, release=release, config=wf_config, trigger=trigger, event=event
    )
    logger.info(
        "Webhook 触发成功",
        extra={
            "route_key": route_key,
            "app_id": str(app.id),
            "release_id": str(release.id),
            "workflow_config_id": str(wf_config.id),
            "trigger_id": trigger.get("id"),
            "method": request.method.upper(),
            "client_ip": request.client.host if request.client else None,
        },
    )

    response_config = ((trigger.get("config") or {}).get("response") or {})
    response_body = _parse_custom_response_body(response_config.get("body"))
    response_status = int(response_config.get("status_code") or 200)
    if response_body is not None:
        return JSONResponse(response_body, status_code=response_status)

    return JSONResponse(result, status_code=response_status)
