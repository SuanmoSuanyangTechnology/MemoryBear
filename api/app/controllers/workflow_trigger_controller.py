"""Workflow Trigger API."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.services.workflow_service import WorkflowService, get_workflow_service

router = APIRouter(prefix="/workflows/triggers", tags=["Workflow Trigger"])


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


@router.api_route("/webhook/{route_key}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def trigger_webhook(
    route_key: str,
    request: Request,
    workflow_service: Annotated[WorkflowService, Depends(get_workflow_service)],
):
    matched = workflow_service.find_published_webhook_trigger(route_key)
    if not matched:
        raise BusinessException("Webhook 触发器不存在", BizCode.NOT_FOUND)

    _, _, _, trigger = matched
    config = trigger.get("config") or {}
    expected_method = str(config.get("method", "POST")).upper()
    if request.method.upper() != expected_method:
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
    result, trigger = await workflow_service.invoke_webhook_trigger(route_key, event)

    response_config = ((trigger.get("config") or {}).get("response") or {})
    response_body = _parse_custom_response_body(response_config.get("body"))
    response_status = int(response_config.get("status_code") or 200)
    if response_body is not None:
        return JSONResponse(response_body, status_code=response_status)

    return JSONResponse(result, status_code=response_status)
