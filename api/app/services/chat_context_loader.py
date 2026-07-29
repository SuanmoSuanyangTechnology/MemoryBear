"""Phase 1: Unified chat context loaders.

Each loader function opens NO session of its own — the caller passes in an
``AsyncSession`` and manages its lifecycle.  Everything returned is a plain
``ChatLoadContext`` with zero ORM references, ready for Phase 2 (zero-DB streaming).

Design doc: docs/方案设计_高并发性能优化.md Section 4.2
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.app_model import App
from app.models.app_release_model import AppRelease
from app.models.conversation_model import Conversation, Message
from app.models.end_user_model import EndUser
from app.models.knowledge_model import Knowledge
from app.models.models_model import ModelApiKey, ModelConfig
from app.models.workspace_model import Workspace
from app.core.exceptions import ResourceNotFoundException
from app.services.chat_context import ApiKeySnapshot, ChatLoadContext
from app.utils.redis_cache import CACHE_MISS, get_json_async, set_json_async

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _load_history_messages(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Load recent messages for a conversation.

    Uses ``idx_messages_conv_created`` for efficient index-only scan.
    """
    result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.is_deleted == False,  # noqa: E712
            Message.is_current == True,  # noqa: E712
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = result.scalars().all()
    history: list[dict[str, Any]] = []
    for msg in reversed(messages):
        history.append({
            "role": msg.role,
            "content": msg.content,
            "meta_data": msg.meta_data,
        })
    return history


def _api_key_snapshot_from_dict(data: dict) -> ApiKeySnapshot:
    """Reconstruct ApiKeySnapshot from cached JSON dict (id is stored as str)."""
    if data.get("id") and isinstance(data["id"], str):
        data["id"] = uuid.UUID(data["id"])
    return ApiKeySnapshot(**data)


async def _load_api_key_snapshot(
    db: AsyncSession,
    model_config_id: Optional[uuid.UUID],
    tenant_id: Optional[uuid.UUID] = None,
) -> ApiKeySnapshot:
    """Load ModelConfig + its ApiKey and return a detached snapshot.

    Cached in Redis (TTL=60s) to avoid per-request DB lookups for stable configs.
    """
    _EMPTY = ApiKeySnapshot(
        id=None, model_name="", provider="", api_key="", api_base="",
        capability=[], is_omni=False,
    )

    if model_config_id is None:
        return _EMPTY

    cache_key = f"cache:v2:api_key_snapshot:{model_config_id}"
    cached = await get_json_async(cache_key)
    if cached is not CACHE_MISS:
        try:
            return _api_key_snapshot_from_dict(cached)
        except Exception:
            logger.warning("Failed to deserialize cached ApiKeySnapshot, will reload from DB")

    result = await db.execute(
        select(ModelConfig)
        .where(ModelConfig.id == model_config_id)
        .options(selectinload(ModelConfig.api_keys))
    )
    model_config = result.scalars().first()

    if model_config is None:
        await set_json_async(cache_key, dataclasses.asdict(_EMPTY), ttl=60)
        return _EMPTY

    api_key: Optional[ModelApiKey] = None
    if model_config.api_keys:
        for ak in model_config.api_keys:
            if ak.is_active:
                api_key = ak
                break
        if api_key is None:
            api_key = model_config.api_keys[0]

    if api_key is None:
        await set_json_async(cache_key, dataclasses.asdict(_EMPTY), ttl=60)
        return _EMPTY

    snapshot = ApiKeySnapshot(
        id=api_key.id,
        model_name=api_key.model_name or "",
        provider=api_key.provider or "",
        api_key=api_key.api_key or "",
        api_base=api_key.api_base or "",
        capability=list(api_key.capability or []),
        is_omni=api_key.is_omni if hasattr(api_key, "is_omni") else False,
    )
    await set_json_async(cache_key, dataclasses.asdict(snapshot), ttl=60)
    return snapshot


async def _load_or_create_end_user(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    other_id: Optional[str],
    app_id: Optional[uuid.UUID] = None,
) -> EndUser:
    """Look up or create an end_user (caller manages advisory lock if needed)."""
    if not other_id:
        other_id = "default_user"

    cache_key = f"cache:v2:end_user:{workspace_id}:{other_id}"
    cached = await get_json_async(cache_key)
    if cached is not CACHE_MISS:
        cached_id = cached.get("id")
        if cached_id:
            from types import SimpleNamespace
            return SimpleNamespace(id=uuid.UUID(cached_id))

    result = await db.execute(
        select(EndUser).where(
            EndUser.workspace_id == workspace_id,
            EndUser.other_id == other_id,
            EndUser.is_active == 1,
        )
    )
    end_user = result.scalars().first()

    if end_user is None:
        end_user = EndUser(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            app_id=app_id,
            other_id=other_id,
            is_active=True,
        )
        db.add(end_user)
        await db.commit()
        await db.refresh(end_user)

    await set_json_async(cache_key, {"id": str(end_user.id)}, ttl=120)
    return end_user


async def _load_workspace_storage_type(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> tuple[Optional[str], Optional[str]]:
    """Return (storage_type, user_rag_memory_id) for a workspace."""
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalars().first()
    storage_type = workspace.storage_type if workspace else None

    user_rag_memory_id: Optional[str] = None
    if storage_type == "rag":
        know_result = await db.execute(
            select(Knowledge).where(
                Knowledge.name == "USER_RAG_MERORY",
                Knowledge.workspace_id == workspace_id,
                Knowledge.status == 1,
            )
        )
        knowledge = know_result.scalars().first()
        if knowledge:
            user_rag_memory_id = str(knowledge.id)

    return storage_type, user_rag_memory_id


async def _ensure_release_attached(
    db: AsyncSession,
    release: AppRelease,
) -> AppRelease:
    """Return *release* attached to *db*, re-querying if detached.

    Callers may pass an ``AppRelease`` loaded in a previous session that was
    committed (and therefore expired).  This helper re-fetches the row in the
    current session so that column access (e.g. ``.config``) does not raise
    ``DetachedInstanceError``.
    """
    from sqlalchemy.orm import object_session
    from sqlalchemy import inspect as sa_inspect

    if object_session(release) is not None:
        return release
    # Use identity key to get id from a potentially expired/detached instance
    identity_key = sa_inspect(release).identity
    release_id = identity_key[0] if identity_key else getattr(release, 'id', None)
    if release_id is None:
        raise ResourceNotFoundException("发布版本", "unknown")
    refreshed = await db.get(AppRelease, release_id)
    if refreshed is None:
        raise ResourceNotFoundException("发布版本", str(release_id))
    return refreshed


async def _ensure_app_attached(db: AsyncSession, app: App) -> App:
    """Re-fetch App in current session if detached/expired.

    Plain dataclass objects with ``id`` and ``type`` attributes (e.g.
    ``_DraftRunAppSnapshot``) are returned as-is since they have no session state.
    """
    if not hasattr(app, '_sa_instance_state'):
        return app
    from sqlalchemy.orm import object_session
    from sqlalchemy import inspect as sa_inspect

    if object_session(app) is not None:
        return app
    identity_key = sa_inspect(app).identity
    app_id = identity_key[0] if identity_key else getattr(app, 'id', None)
    if app_id is None:
        raise ResourceNotFoundException("应用", "unknown")
    refreshed = await db.get(App, app_id)
    if refreshed is None:
        raise ResourceNotFoundException("应用", str(app_id))
    return refreshed


async def _ensure_model_attached(
    db: AsyncSession,
    obj: Any,
    model_class: type,
    label: str = "记录",
) -> Any:
    """Re-fetch any ORM model instance in current session if detached/expired.

    Non-ORM objects (plain dataclasses, stubs) are returned as-is.
    """
    if not hasattr(obj, '_sa_instance_state'):
        return obj
    from sqlalchemy.orm import object_session
    from sqlalchemy import inspect as sa_inspect

    if object_session(obj) is not None:
        return obj
    identity_key = sa_inspect(obj).identity
    obj_id = identity_key[0] if identity_key else getattr(obj, 'id', None)
    if obj_id is None:
        raise ResourceNotFoundException(label, "unknown")
    refreshed = await db.get(model_class, obj_id)
    if refreshed is None:
        raise ResourceNotFoundException(label, str(obj_id))
    return refreshed


# ---------------------------------------------------------------------------
# Agent tool loading (shared by agent-type loaders)
# ---------------------------------------------------------------------------


async def _load_agent_tools(
    release_config: dict[str, Any],
    user_id: str,
    workspace_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    storage_type: str | None,
    user_rag_memory_id: str | None,
    message: str,
    web_search: bool = True,
    memory: bool = True,
) -> tuple[list[Any], str, list[Any], bool]:
    """Load agent tools, skill prompts, citations collector, and memory flag.

    Uses AgentRunService methods which each open their own DB session internally.
    Returns (tools, skill_prompts, citations_collector, memory_enabled).
    """
    from app.services.draft_run_service import AgentRunService

    agent_service = AgentRunService(None)

    # Resolve web_search from features config (features 配置为权威来源)
    features_config = release_config.get("features", {}) or {}
    if isinstance(features_config, dict):
        ws_feature = features_config.get("web_search", {})
        if isinstance(ws_feature, dict) and ws_feature.get("enabled"):
            web_search = True

    tools_config = release_config.get("tools", []) or []
    skills_config = release_config.get("skills", {}) or {}
    knowledge_retrieval_config = release_config.get("knowledge_retrieval")
    memory_config = release_config.get("memory")

    # 1. Base tools + web search
    tools = await agent_service.load_tools_config(
        tools_config, web_search, tenant_id, user_id, workspace_id,
    )

    # 2. Skills
    skill_prompts = ""
    skill_tools, skill_prompts = await agent_service.load_skill_config(
        skills_config, message, tenant_id, user_id, workspace_id,
    )
    tools.extend(skill_tools)

    # 3. Knowledge retrieval
    kb_tools, citations_collector = await agent_service.load_knowledge_retrieval_config(
        knowledge_retrieval_config, user_id,
    )
    tools.extend(kb_tools)

    # 4. Memory
    memory_enabled = False
    if memory:
        memory_tools, memory_enabled = await agent_service.load_memory_config(
            memory_config, user_id, workspace_id, storage_type, user_rag_memory_id,
        )
        tools.extend(memory_tools)

    return tools, skill_prompts, citations_collector, memory_enabled


# ---------------------------------------------------------------------------
# Public loader functions
# ---------------------------------------------------------------------------


async def load_chat_context_for_app_api(
    db: AsyncSession,
    app: App,
    release: AppRelease,
    payload: Any,  # AppChatRequest
    workspace_id: uuid.UUID,
    end_user: EndUser,
    storage_type: Optional[str],
    user_rag_memory_id: Optional[str],
    *,
    tenant_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    is_new_conversation: bool = True,
    history: Optional[list[dict[str, Any]]] = None,
    web_search: bool = True,
    memory: bool = True,
) -> ChatLoadContext:
    """Load Phase 1 context for ``app_api.chat``.

    *conversation_id* and *is_new_conversation* should come from the
    controller's pre-processing (where the conversation was already created).
    When provided, the loader skips the redundant conversation DB round-trip.
    *tenant_id* should be obtained from the auth context (ApiKeyAuth) or
    workspace lookup before calling — the loader never queries workspace.
    """
    from app.models.annotation_model import AppAnnotation, AppAnnotationSetting

    release = await _ensure_release_attached(db, release)
    release_config: dict[str, Any] = release.config or {}
    app_type = app.type

    # -- conversation (from controller pre-processing, no DB query) --------------
    if conversation_id is None:
        conversation_id = uuid.uuid4()

    # -- tenant (from auth context, no DB query) --------------------------------
    # tenant_id is passed by the caller — never queried from workspace here.

    # -- model / api key --------------------------------------------------------
    default_model_config_id = release_config.get("default_model_config_id")
    if default_model_config_id and isinstance(default_model_config_id, str):
        default_model_config_id = uuid.UUID(default_model_config_id)
    api_key = await _load_api_key_snapshot(db, default_model_config_id, tenant_id)

    # -- system prompt & model parameters (from release config) -----------------
    system_prompt = release_config.get("system_prompt", "") or ""
    model_parameters: dict[str, Any] = release_config.get("model_parameters", {}) or {}
    features_config: dict[str, Any] = release_config.get("features", {}) or {}

    # -- annotation match -------------------------------------------------------
    annotation_match: Optional[dict[str, Any]] = None
    message = getattr(payload, "message", "")
    if message:
        annot_result = await db.execute(
            select(AppAnnotationSetting).where(
                AppAnnotationSetting.app_id == app.id,
                AppAnnotationSetting.enabled == 1,
            )
        )
        annot_setting = annot_result.scalars().first()
        if annot_setting:
            annots_result = await db.execute(
                select(AppAnnotation).where(
                    AppAnnotation.app_id == app.id,
                    AppAnnotation.is_active == 1,
                )
            )
            annotations = annots_result.scalars().all()
            for annotation in annotations:
                if annotation.keyword and annotation.keyword in message:
                    annotation_match = {
                        "answer": annotation.answer or "",
                        "keyword": annotation.keyword,
                    }
                    break

    # -- history ----------------------------------------------------------------
    if history is None:
        history = await _load_history_messages(db, conversation_id, limit=settings.AGENT_MAX_HISTORY)

    # -- opening statement ------------------------------------------------------
    opening_statement: Optional[str] = None
    opening_suggested_questions: list[str] = []
    if is_new_conversation and isinstance(features_config, dict):
        opening_stmt_cfg = features_config.get("opening_statement")
        if isinstance(opening_stmt_cfg, dict):
            opening_statement = opening_stmt_cfg.get("text") or ""
            opening_suggested_questions = opening_stmt_cfg.get("suggested_questions") or []

    # -- tools / skills / knowledge / memory ------------------------------------
    user_id_str = str(end_user.id) if end_user else "default_user"
    tools, skill_prompts, citations_collector, memory_enabled = await _load_agent_tools(
        release_config=release_config,
        user_id=user_id_str,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        message=message,
        web_search=web_search,
        memory=memory,
    )

    return ChatLoadContext(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        app_id=app.id,
        user_id=user_id_str,
        tenant_id=tenant_id,
        api_key=api_key,
        system_prompt=system_prompt,
        model_parameters=model_parameters,
        features_config=features_config,
        tools=tools,
        skill_prompts=skill_prompts,
        citations_collector=citations_collector,
        memory_enabled=memory_enabled,
        history=history,
        is_new_conversation=is_new_conversation,
        opening_statement=opening_statement,
        opening_suggested_questions=list(opening_suggested_questions),
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id or "",
        annotation_match=annotation_match,
        source="api",
    )


async def load_chat_context_for_draft_run(
    db: AsyncSession,
    app: App,
    payload: Any,  # DraftRunRequest
    release: Optional[AppRelease],
    workspace_id: uuid.UUID,
    end_user: EndUser,
    *,
    tenant_id: Optional[uuid.UUID] = None,
    storage_type: Optional[str] = None,
    user_rag_memory_id: Optional[str] = None,
) -> ChatLoadContext:
    """Load Phase 1 context for ``draft_run`` (AGENT / MULTI_AGENT paths).

    The caller is responsible for loading the *app* snapshot, *end_user*, and
    optional *release* before calling this function.
    """
    app = await _ensure_app_attached(db, app)
    if release is not None:
        release = await _ensure_release_attached(db, release)
    if end_user is not None:
        end_user = await _ensure_model_attached(db, end_user, EndUser, "用户")
    release_config: dict[str, Any] = release.config if release and release.config else {}
    app_type = app.type

    # -- conversation (draft) ---------------------------------------------------
    conversation_id = uuid.uuid4()
    is_new_conversation = True
    conv_payload_id = getattr(payload, "conversation_id", None)
    if conv_payload_id:
        conv_result = await db.execute(
            select(Conversation).where(Conversation.id == conv_payload_id)
        )
        conversation = conv_result.scalars().first()
        if conversation:
            conversation_id = conversation.id
            is_new_conversation = False
    if is_new_conversation:
        conversation = Conversation(
            id=conversation_id,
            app_id=app.id,
            workspace_id=workspace_id,
            user_id=str(end_user.id) if end_user else None,
            is_draft=True,
        )
        db.add(conversation)
        await db.commit()

    # -- tenant (from auth context, no DB query) --------------------------------

    # -- model / api key --------------------------------------------------------
    default_model_config_id = release_config.get("default_model_config_id")
    if isinstance(default_model_config_id, str):
        default_model_config_id = uuid.UUID(default_model_config_id)
    elif default_model_config_id is None and release:
        default_model_config_id = release.default_model_config_id
    api_key = await _load_api_key_snapshot(db, default_model_config_id, tenant_id)

    # -- system prompt & parameters (from release config) -----------------------
    system_prompt = release_config.get("system_prompt", "") or ""
    model_parameters: dict[str, Any] = release_config.get("model_parameters", {}) or {}
    features_config: dict[str, Any] = release_config.get("features", {}) or {}

    # -- history ----------------------------------------------------------------
    history = await _load_history_messages(db, conversation_id, limit=settings.AGENT_MAX_HISTORY)

    # -- opening statement ------------------------------------------------------
    opening_statement: Optional[str] = None
    opening_suggested_questions: list[str] = []
    if is_new_conversation and isinstance(features_config, dict):
        opening_stmt_cfg = features_config.get("opening_statement")
        if isinstance(opening_stmt_cfg, dict):
            opening_statement = opening_stmt_cfg.get("text") or ""
            opening_suggested_questions = opening_stmt_cfg.get("suggested_questions") or []

    return ChatLoadContext(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        app_id=app.id,
        user_id=str(end_user.id) if end_user else "default_user",
        tenant_id=tenant_id,
        api_key=api_key,
        system_prompt=system_prompt,
        model_parameters=model_parameters,
        features_config=features_config,
        history=history,
        is_new_conversation=is_new_conversation,
        opening_statement=opening_statement,
        opening_suggested_questions=list(opening_suggested_questions),
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id or "",
        source="console",
    )


async def load_chat_context_for_public_share(
    db: AsyncSession,
    share: Any,  # ReleaseShare
    release: AppRelease,
    app: App,
    payload: Any,  # ChatRequest
    workspace_id: uuid.UUID,
    end_user: EndUser,
    *,
    tenant_id: Optional[uuid.UUID] = None,
    storage_type: Optional[str] = None,
    user_rag_memory_id: Optional[str] = None,
    web_search: bool = True,
    memory: bool = True,
) -> ChatLoadContext:
    """Load Phase 1 context for ``public_share.chat``.

    The caller must already have authenticated the share token and loaded
    *share*, *release*, *app*, and *end_user*.
    """
    release = await _ensure_release_attached(db, release)
    app = await _ensure_app_attached(db, app)
    if end_user is not None:
        end_user = await _ensure_model_attached(db, end_user, EndUser, "用户")
    release_config: dict[str, Any] = release.config or {}

    # -- conversation -----------------------------------------------------------
    conversation_id = uuid.uuid4()
    is_new_conversation = True
    conv_payload_id = getattr(payload, "conversation_id", None)
    if conv_payload_id:
        conv_result = await db.execute(
            select(Conversation).where(Conversation.id == conv_payload_id)
        )
        conversation = conv_result.scalars().first()
        if conversation:
            conversation_id = conversation.id
            is_new_conversation = False
    if is_new_conversation:
        conversation = Conversation(
            id=conversation_id,
            app_id=app.id,
            workspace_id=workspace_id,
            user_id=str(end_user.id) if end_user else None,
            is_draft=False,
        )
        db.add(conversation)
        await db.commit()

    # -- tenant (from auth context, no DB query) --------------------------------

    # -- model / api key --------------------------------------------------------
    default_model_config_id = release_config.get("default_model_config_id") or release.default_model_config_id
    if isinstance(default_model_config_id, str):
        default_model_config_id = uuid.UUID(default_model_config_id)
    api_key = await _load_api_key_snapshot(db, default_model_config_id, tenant_id)

    # -- system prompt & parameters ---------------------------------------------
    system_prompt = release_config.get("system_prompt", "") or ""
    model_parameters: dict[str, Any] = release_config.get("model_parameters", {}) or {}
    features_config: dict[str, Any] = release_config.get("features", {}) or {}

    # -- history ----------------------------------------------------------------
    history = await _load_history_messages(db, conversation_id, limit=settings.AGENT_MAX_HISTORY)

    # -- opening statement ------------------------------------------------------
    opening_statement: Optional[str] = None
    opening_suggested_questions: list[str] = []
    if is_new_conversation and isinstance(features_config, dict):
        opening_stmt_cfg = features_config.get("opening_statement")
        if isinstance(opening_stmt_cfg, dict):
            opening_statement = opening_stmt_cfg.get("text") or ""
            opening_suggested_questions = opening_stmt_cfg.get("suggested_questions") or []

    # -- tools / skills / knowledge / memory ------------------------------------
    user_id_str = str(end_user.id) if end_user else "default_user"
    message = getattr(payload, "message", "") or ""
    tools, skill_prompts, citations_collector, memory_enabled = await _load_agent_tools(
        release_config=release_config,
        user_id=user_id_str,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id,
        message=message,
        web_search=web_search,
        memory=memory,
    )

    return ChatLoadContext(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        app_id=app.id,
        user_id=user_id_str,
        tenant_id=tenant_id,
        api_key=api_key,
        system_prompt=system_prompt,
        model_parameters=model_parameters,
        features_config=features_config,
        tools=tools,
        skill_prompts=skill_prompts,
        citations_collector=citations_collector,
        memory_enabled=memory_enabled,
        history=history,
        is_new_conversation=is_new_conversation,
        opening_statement=opening_statement,
        opening_suggested_questions=list(opening_suggested_questions),
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id or "",
        source="share",
    )


async def load_chat_context_for_draft_compare(
    db: AsyncSession,
    app: App,
    payload: Any,  # DraftRunCompareRequest
    workspace_id: uuid.UUID,
    end_user: EndUser,
    *,
    tenant_id: Optional[uuid.UUID] = None,
    storage_type: Optional[str] = None,
    user_rag_memory_id: Optional[str] = None,
    model_config_snapshots: Optional[list[ApiKeySnapshot]] = None,
) -> ChatLoadContext:
    """Load Phase 1 context for ``draft_run_compare``.

    *model_config_snapshots* should be pre-loaded for each model in
    ``payload.models`` by the caller (each requiring a DB lookup).
    """
    app = await _ensure_app_attached(db, app)
    # -- conversation (draft) ---------------------------------------------------
    conversation_id = uuid.uuid4()
    is_new_conversation = True
    conv_payload_id = getattr(payload, "conversation_id", None)
    if conv_payload_id:
        conv_result = await db.execute(
            select(Conversation).where(Conversation.id == conv_payload_id)
        )
        conversation = conv_result.scalars().first()
        if conversation:
            conversation_id = conversation.id
            is_new_conversation = False
    if is_new_conversation:
        conversation = Conversation(
            id=conversation_id,
            app_id=app.id,
            workspace_id=workspace_id,
            user_id=str(end_user.id) if end_user else None,
            is_draft=True,
        )
        db.add(conversation)
        await db.commit()

    # -- tenant (from auth context, no DB query) --------------------------------

    # -- use first model config as primary api_key snapshot ---------------------
    api_key = (
        model_config_snapshots[0]
        if model_config_snapshots
        else await _load_api_key_snapshot(db, None, tenant_id)
    )

    # -- history ----------------------------------------------------------------
    history = await _load_history_messages(db, conversation_id, limit=settings.AGENT_MAX_HISTORY)

    return ChatLoadContext(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        app_id=app.id,
        user_id=str(end_user.id) if end_user else "default_user",
        tenant_id=tenant_id,
        api_key=api_key,
        system_prompt="",
        model_parameters={},
        features_config={},
        history=history,
        is_new_conversation=is_new_conversation,
        storage_type=storage_type,
        user_rag_memory_id=user_rag_memory_id or "",
        source="console",
    )
