"""Template management routes"""
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header

from app.config import get_settings
from app.models import BuildTemplateRequest, TemplateInfo

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_api_key(x_api_key: Optional[str] = Header(None)):
    settings = get_settings()
    if x_api_key != settings.API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/templates", response_model=list[TemplateInfo])
async def list_templates(
    request: Request,
    x_api_key: Optional[str] = Header(None),
):
    """List all available templates"""
    _verify_api_key(x_api_key)
    redis_store = request.app.state.redis_store
    templates = await redis_store.list_templates()
    return templates


@router.get("/templates/{template_id}", response_model=TemplateInfo)
async def get_template(
    request: Request,
    template_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Get template details"""
    _verify_api_key(x_api_key)
    redis_store = request.app.state.redis_store
    template = await redis_store.get_template(template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    return template


@router.post("/templates/build", response_model=TemplateInfo)
async def build_template(
    request: Request,
    body: BuildTemplateRequest,
    x_api_key: Optional[str] = Header(None),
):
    """Build a new template from Dockerfile

    This triggers an async build process. The template status will be
    'building' initially, then transition to 'ready' or 'error'.
    """
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    
    try:
        template = await sandbox_manager.build_template(body)
        return template
    except Exception as e:
        logger.error(f"Failed to build template: {e}", exc_info=True)
        raise HTTPException(500, f"Template build failed: {str(e)}")


@router.delete("/templates/{template_id}")
async def delete_template(
    request: Request,
    template_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Delete a template"""
    _verify_api_key(x_api_key)
    redis_store = request.app.state.redis_store
    
    template = await redis_store.get_template(template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    
    await redis_store.delete_template(template_id)
    return {"status": "deleted", "template_id": template_id}
