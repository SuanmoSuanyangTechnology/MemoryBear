import json
import uuid
from typing import Any, Optional

from app.aioRedis import get_redis_connection
from app.core.logging_config import get_business_logger
from app.core.utils.datetime_utils import parse_iso_to_utc_naive, to_iso_z
from app.plugins import get_plugin
from app.repositories.context_state_repository import ContextStateRepository
from app.services.conversation_service import ConversationService

logger = get_business_logger()


class ContextCacheRepository:
    """上下文状态 Redis 热缓存。"""

    KEY_PREFIX = "ctx"

    @classmethod
    def _make_key(cls, conversation_id: uuid.UUID, scope_key: str) -> str:
        return f"{cls.KEY_PREFIX}:{conversation_id}:{scope_key}"

    @staticmethod
    def _deserialize_datetime(value: Optional[str]):
        if not value:
            return None
        try:
            return parse_iso_to_utc_naive(value)
        except Exception:
            return None

    @staticmethod
    def _serialize_state(state: dict[str, Any]) -> dict[str, Any]:
        payload = dict(state)
        if payload.get("summarized_until_at") is not None:
            payload["summarized_until_at"] = to_iso_z(payload["summarized_until_at"])
        return payload

    @staticmethod
    def _deserialize_state(state: dict[str, Any]) -> dict[str, Any]:
        payload = dict(state)
        if payload.get("summarized_until_at"):
            payload["summarized_until_at"] = ContextCacheRepository._deserialize_datetime(
                payload["summarized_until_at"]
            )
        return payload

    async def get(
            self,
            conversation_id: uuid.UUID,
            scope_key: str,
    ) -> Optional[dict[str, Any]]:
        conn = await get_redis_connection()
        if conn is None:
            return None
        try:
            raw = await conn.get(self._make_key(conversation_id, scope_key))
            if not raw:
                return None
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            return self._deserialize_state(data)
        except Exception as exc:
            logger.warning("Load context cache failed: %s", exc)
            return None

    async def set(self, state: dict[str, Any]) -> None:
        conn = await get_redis_connection()
        if conn is None:
            return
        try:
            key = self._make_key(uuid.UUID(str(state["conversation_id"])), state["scope_key"])
            payload = self._serialize_state(state)
            await conn.set(key, json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as exc:
            logger.warning("Save context cache failed: %s", exc)

    async def delete(
            self,
            conversation_id: uuid.UUID,
            scope_key: str,
    ) -> None:
        conn = await get_redis_connection()
        if conn is None:
            return
        try:
            await conn.delete(self._make_key(conversation_id, scope_key))
        except Exception as exc:
            logger.warning("Delete context cache failed: %s", exc)


class ContextEngineManager:
    """上下文引擎协调器。

    核心职责：
    - 读取 `features.context_engine`
    - 通过插件获取 premium provider
    - 维护 DB/Redis 中的摘要状态
    - 对聊天链路与 Workflow 节点提供统一适配
    """

    DEFAULT_PROVIDER = "context_manager.incremental_summary"

    def __init__(self, db):
        self.db = db
        self.state_repo = ContextStateRepository(db)
        self.cache_repo = ContextCacheRepository()
        self.conversation_service = ConversationService(db)

    @staticmethod
    def _normalize_features(features: Any) -> dict[str, Any]:
        if hasattr(features, "model_dump"):
            return features.model_dump()
        return features or {}

    def _get_context_config(self, features: Any) -> Optional[dict[str, Any]]:
        features_config = self._normalize_features(features)
        context_config = features_config.get("context_engine")
        if hasattr(context_config, "model_dump"):
            context_config = context_config.model_dump()
        if not isinstance(context_config, dict) or not context_config.get("enabled"):
            return None
        return context_config

    @staticmethod
    def _should_degrade(_context_config: Optional[dict[str, Any]]) -> bool:
        return True

    def _resolve_provider(self, _context_config: dict[str, Any]):
        plugin = get_plugin("context_engine")
        if plugin is None or not hasattr(plugin, "get_provider"):
            return None, None
        provider_name = self.DEFAULT_PROVIDER
        return plugin.get_provider(provider_name), provider_name

    @staticmethod
    def _build_options(
            _context_config: dict[str, Any],
            *,
            window_size: Optional[int] = None,
            force_window_size: bool = False,
            model_config_id: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if force_window_size and window_size and window_size > 0:
            options["window_size"] = window_size
        elif (not options.get("window_size")) and window_size and window_size > 0:
            options["window_size"] = window_size
        if model_config_id:
            options["model_config_id"] = str(model_config_id)
        options.setdefault("window_size", 6)
        options.setdefault("summary_prefix", "[此前对话摘要]")
        options.setdefault("cache_backend", "redis")
        options.setdefault("cache_ttl_seconds", 7200)
        return options

    @staticmethod
    def _build_session_id(conversation_id: uuid.UUID, scope_key: str) -> str:
        return f"{conversation_id}:{scope_key}"

    @staticmethod
    def _state_to_dict(state) -> dict[str, Any]:
        return {
            "conversation_id": str(state.conversation_id),
            "scope_key": state.scope_key,
            "source_type": state.source_type,
            "summary_text": state.summary_text,
            "summarized_until_message_id": str(state.summarized_until_message_id) if state.summarized_until_message_id else None,
            "summarized_until_at": state.summarized_until_at,
            "summarized_until_seq": state.summarized_until_seq,
        }

    async def _get_state(
            self,
            conversation_id: uuid.UUID,
            scope_key: str,
    ) -> Optional[dict[str, Any]]:
        cached = await self.cache_repo.get(conversation_id, scope_key)
        if cached:
            return cached
        state = self.state_repo.get(conversation_id=conversation_id, scope_key=scope_key)
        if state is None:
            return None
        payload = self._state_to_dict(state)
        await self.cache_repo.set(payload)
        return payload

    async def _upsert_state(
            self,
            *,
            conversation_id: uuid.UUID,
            scope_key: str,
            source_type: str,
            summary_text: Optional[str],
            summarized_until_message_id: Optional[uuid.UUID] = None,
            summarized_until_at=None,
            summarized_until_seq: Optional[int] = None,
    ) -> None:
        state = self.state_repo.upsert(
            conversation_id=conversation_id,
            scope_key=scope_key,
            source_type=source_type,
            summary_text=summary_text,
            summarized_until_message_id=summarized_until_message_id,
            summarized_until_at=summarized_until_at,
            summarized_until_seq=summarized_until_seq,
        )
        self.db.commit()
        await self.cache_repo.set(self._state_to_dict(state))

    @staticmethod
    def _normalize_prepared_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for msg in messages or []:
            role = msg.get("role")
            if hasattr(role, "value"):
                role = role.value
            normalized.append({
                "role": str(role or "user"),
                "content": msg.get("content", ""),
            })
        return normalized

    @staticmethod
    def _split_agent_messages(
            prepared_messages: list[dict[str, Any]],
            fallback_system_prompt: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        messages = list(prepared_messages or [])
        if messages and messages[-1].get("role") == "user":
            messages = messages[:-1]

        system_parts: list[str] = []
        history: list[dict[str, Any]] = []
        seen_non_system = False
        for msg in messages:
            role = str(msg.get("role") or "user")
            if role == "system" and not seen_non_system:
                system_parts.append(str(msg.get("content") or ""))
                continue
            seen_non_system = True
            if role in ("user", "assistant"):
                history.append({"role": role, "content": msg.get("content", "")})

        system_prompt = "\n\n".join(part for part in system_parts if part).strip() or fallback_system_prompt
        return system_prompt, history

    @staticmethod
    def _serialize_history_message(msg, current_provider: Optional[str], current_is_omni: Optional[bool]) -> dict[str, Any]:
        history_files = msg.meta_data.get("history_files", {}) if msg.meta_data else {}
        has_files = bool(history_files and current_provider and current_is_omni is not None)
        if has_files:
            stored_provider = history_files.get("provider")
            stored_is_omni = history_files.get("is_omni")
            if stored_provider == current_provider and stored_is_omni == current_is_omni:
                content: Any = [{"type": "text", "text": msg.content}]
                content.extend(history_files.get("content", []))
            else:
                content = msg.content
        else:
            content = msg.content
        return {"role": msg.role, "content": content}

    @staticmethod
    def _get_cross_session_recent_limit(context_config: dict[str, Any]) -> int:
        if not context_config.get("cross_session_recent_enabled"):
            return 0
        try:
            return max(int(context_config.get("cross_session_recent_limit") or 0), 0)
        except (TypeError, ValueError):
            return 0

    def _get_cross_session_recent_records(
            self,
            *,
            conversation_id: uuid.UUID,
            context_config: dict[str, Any],
    ) -> list[Any]:
        limit = self._get_cross_session_recent_limit(context_config)
        if limit <= 0:
            return []

        conversation = self.conversation_service.conversation_repo.get_conversation_by_conversation_id(
            conversation_id
        )
        if not conversation or not getattr(conversation, "user_id", None):
            return []

        return self.conversation_service.message_repo.get_recent_messages_from_other_conversations(
            app_id=conversation.app_id,
            user_id=conversation.user_id,
            exclude_conversation_id=conversation_id,
            limit=limit,
        )

    @staticmethod
    def _trim_messages_after_boundary(messages: list[Any], state: Optional[dict[str, Any]]) -> list[Any]:
        if not state:
            return list(messages)
        boundary_id = state.get("summarized_until_message_id")
        boundary_at = state.get("summarized_until_at")
        if not boundary_id and not boundary_at:
            return list(messages)

        if boundary_id:
            trimmed = []
            found_boundary = False
            for message in messages:
                if not found_boundary:
                    if str(message.id) == boundary_id:
                        found_boundary = True
                    continue
                trimmed.append(message)
            if found_boundary:
                return trimmed

        if boundary_at:
            return [message for message in messages if message.created_at > boundary_at]
        return list(messages)

    @staticmethod
    def _normalize_workflow_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, message in enumerate(messages or [], start=1):
            if not isinstance(message, dict):
                continue
            normalized.append({
                "seq": index,
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
            })
        return normalized

    @staticmethod
    def _trim_workflow_messages_after_seq(
            messages: list[dict[str, Any]],
            summarized_until_seq: Optional[int],
    ) -> list[dict[str, Any]]:
        if not summarized_until_seq:
            return list(messages)
        return [message for message in messages if int(message.get("seq") or 0) > summarized_until_seq]

    @staticmethod
    def _strip_workflow_seq(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"role": message.get("role", "user"), "content": message.get("content", "")} for message in messages]

    async def prepare_app_agent_input(
            self,
            *,
            features: Any,
            conversation_id: uuid.UUID,
            system_prompt: str,
            current_input: str,
            current_provider: Optional[str],
            current_is_omni: Optional[bool],
            legacy_max_history: int = 10,
            scope_key: str = "conversation",
            model_config_id: str | uuid.UUID | None = None,
    ) -> Optional[tuple[str, list[dict[str, Any]]]]:
        context_config = self._get_context_config(features)
        if context_config is None:
            return None

        provider, provider_name = self._resolve_provider(context_config)
        if provider is None:
            logger.info("Context engine plugin unavailable, fallback to legacy history")
            return None

        try:
            state = await self._get_state(conversation_id, scope_key)
            messages = self.conversation_service.message_repo.get_messages_since(
                conversation_id=conversation_id,
                since_at=state.get("summarized_until_at") if state else None,
            )
            cross_session_records = self._get_cross_session_recent_records(
                conversation_id=conversation_id,
                context_config=context_config,
            )
            recent_records = self._trim_messages_after_boundary(messages, state)
            recent_messages = [
                self._serialize_history_message(msg, current_provider, current_is_omni)
                for msg in cross_session_records
            ]
            recent_messages.extend([
                self._serialize_history_message(msg, current_provider, current_is_omni)
                for msg in recent_records
            ])
            options = self._build_options(
                context_config,
                window_size=legacy_max_history,
                model_config_id=model_config_id,
            )
            prepared = await provider.prepare_messages(
                session_id=self._build_session_id(conversation_id, scope_key),
                system_prompt=system_prompt,
                current_input=current_input,
                recent_messages=recent_messages,
                summary_text=state.get("summary_text") if state else None,
                options=options,
            )
            normalized = self._normalize_prepared_messages(prepared)
            logger.info(
                "Prepared app context via provider",
                extra={
                    "conversation_id": str(conversation_id),
                    "scope_key": scope_key,
                    "provider": provider_name,
                    "cross_session_count": len(cross_session_records),
                    "recent_count": len(recent_messages),
                    "prepared_count": len(normalized),
                }
            )
            return self._split_agent_messages(normalized, system_prompt)
        except Exception as exc:
            if self._should_degrade(context_config):
                logger.warning("Prepare app context failed, fallback to legacy history: %s", exc)
                return None
            raise

    async def after_app_turn(
            self,
            *,
            features: Any,
            conversation_id: uuid.UUID,
            current_provider: Optional[str],
            current_is_omni: Optional[bool],
            legacy_max_history: int = 10,
            scope_key: str = "conversation",
            model_config_id: str | uuid.UUID | None = None,
    ) -> bool:
        context_config = self._get_context_config(features)
        if context_config is None:
            return False

        provider, provider_name = self._resolve_provider(context_config)
        if provider is None:
            return False

        try:
            state = await self._get_state(conversation_id, scope_key)
            messages = self.conversation_service.message_repo.get_messages_since(
                conversation_id=conversation_id,
                since_at=state.get("summarized_until_at") if state else None,
            )
            recent_records = self._trim_messages_after_boundary(messages, state)
            options = self._build_options(
                context_config,
                window_size=legacy_max_history,
                model_config_id=model_config_id,
            )
            window_size = int(options.get("window_size") or legacy_max_history or 10)
            if len(recent_records) <= window_size:
                if state and state.get("summary_text"):
                    await provider.prime_summary(
                        session_id=self._build_session_id(conversation_id, scope_key),
                        summary_text=state["summary_text"],
                        options=options,
                    )
                return False

            recent_messages = [
                self._serialize_history_message(msg, current_provider, current_is_omni)
                for msg in recent_records
            ]
            # close() 前预读 boundary_message 属性，防止 close 后 DetachedInstanceError
            boundary_message = recent_records[-window_size - 1]
            _boundary_id = boundary_message.id
            _boundary_at = boundary_message.created_at
            # LLM 调用期间不需要 db，提前归还连接给连接池
            self.db.close()
            summary_text = await provider.warm_up_summary(
                session_id=self._build_session_id(conversation_id, scope_key),
                full_history=recent_messages,
                summary_text=state.get("summary_text") if state else None,
                options=options,
            )
            if not summary_text:
                return False

            await self._upsert_state(
                conversation_id=conversation_id,
                scope_key=scope_key,
                source_type="conversation",
                summary_text=summary_text,
                summarized_until_message_id=_boundary_id,
                summarized_until_at=_boundary_at,
            )
            await provider.prime_summary(
                session_id=self._build_session_id(conversation_id, scope_key),
                summary_text=summary_text,
                options=options,
            )
            logger.info(
                "Updated app context state",
                extra={
                    "conversation_id": str(conversation_id),
                    "scope_key": scope_key,
                    "provider": provider_name,
                    "window_size": window_size,
                    "recent_count": len(recent_messages),
                    "boundary_message_id": str(_boundary_id),
                }
            )
            return True
        except Exception as exc:
            if self._should_degrade(context_config):
                logger.warning("Update app context failed, ignored: %s", exc)
                return False
            raise

    async def prepare_workflow_history_prefix(
            self,
            *,
            features: Any,
            conversation_id: str | uuid.UUID | None,
            scope_key: str,
            current_input: str,
            workflow_messages: list[dict[str, Any]],
            window_size: int,
            model_config_id: str | uuid.UUID | None = None,
    ) -> Optional[list[dict[str, Any]]]:
        if not conversation_id:
            return None

        context_config = self._get_context_config(features)
        if context_config is None:
            return None

        provider, provider_name = self._resolve_provider(context_config)
        if provider is None:
            return None

        conversation_uuid = uuid.UUID(str(conversation_id))
        try:
            state = await self._get_state(conversation_uuid, scope_key)
            normalized_messages = self._normalize_workflow_messages(workflow_messages)
            cross_session_records = self._get_cross_session_recent_records(
                conversation_id=conversation_uuid,
                context_config=context_config,
            )
            recent_messages = self._trim_workflow_messages_after_seq(
                normalized_messages,
                state.get("summarized_until_seq") if state else None,
            )
            provider_recent_messages = [
                self._serialize_history_message(msg, None, None)
                for msg in cross_session_records
            ]
            provider_recent_messages.extend(self._strip_workflow_seq(recent_messages))
            prepared = await provider.prepare_messages(
                session_id=self._build_session_id(conversation_uuid, scope_key),
                system_prompt=None,
                current_input=current_input,
                recent_messages=provider_recent_messages,
                summary_text=state.get("summary_text") if state else None,
                options=self._build_options(
                    context_config,
                    window_size=window_size,
                    force_window_size=True,
                    model_config_id=model_config_id,
                ),
            )
            normalized = self._normalize_prepared_messages(prepared)
            if normalized and normalized[-1].get("role") == "user":
                normalized = normalized[:-1]
            logger.info(
                "Prepared workflow context via provider",
                extra={
                    "conversation_id": str(conversation_uuid),
                    "scope_key": scope_key,
                    "provider": provider_name,
                    "cross_session_count": len(cross_session_records),
                    "recent_count": len(provider_recent_messages),
                    "prepared_count": len(normalized),
                }
            )
            return normalized
        except Exception as exc:
            if self._should_degrade(context_config):
                logger.warning("Prepare workflow context failed, fallback to legacy history: %s", exc)
                return None
            raise

    async def after_workflow_turn(
            self,
            *,
            features: Any,
            conversation_id: str | uuid.UUID | None,
            scope_key: str,
            workflow_messages: list[dict[str, Any]],
            window_size: int,
            model_config_id: str | uuid.UUID | None = None,
    ) -> bool:
        if not conversation_id:
            return False

        context_config = self._get_context_config(features)
        if context_config is None:
            return False

        provider, provider_name = self._resolve_provider(context_config)
        if provider is None:
            return False

        conversation_uuid = uuid.UUID(str(conversation_id))
        try:
            state = await self._get_state(conversation_uuid, scope_key)
            normalized_messages = self._normalize_workflow_messages(workflow_messages)
            recent_messages = self._trim_workflow_messages_after_seq(
                normalized_messages,
                state.get("summarized_until_seq") if state else None,
            )
            effective_window = max(int(window_size or 0), 1)
            if len(recent_messages) <= effective_window:
                if state and state.get("summary_text"):
                    await provider.prime_summary(
                        session_id=self._build_session_id(conversation_uuid, scope_key),
                        summary_text=state["summary_text"],
                        options=self._build_options(
                            context_config,
                            window_size=effective_window,
                            force_window_size=True,
                            model_config_id=model_config_id,
                        ),
                    )
                return False

            options = self._build_options(
                context_config,
                window_size=effective_window,
                force_window_size=True,
                model_config_id=model_config_id,
            )
            summary_text = await provider.warm_up_summary(
                session_id=self._build_session_id(conversation_uuid, scope_key),
                full_history=self._strip_workflow_seq(recent_messages),
                summary_text=state.get("summary_text") if state else None,
                options=options,
            )
            if not summary_text:
                return False

            boundary_message = recent_messages[-effective_window - 1]
            await self._upsert_state(
                conversation_id=conversation_uuid,
                scope_key=scope_key,
                source_type="workflow",
                summary_text=summary_text,
                summarized_until_seq=int(boundary_message["seq"]),
            )
            await provider.prime_summary(
                session_id=self._build_session_id(conversation_uuid, scope_key),
                summary_text=summary_text,
                options=options,
            )
            logger.info(
                "Updated workflow context state",
                extra={
                    "conversation_id": str(conversation_uuid),
                    "scope_key": scope_key,
                    "provider": provider_name,
                    "window_size": effective_window,
                    "recent_count": len(recent_messages),
                    "summarized_until_seq": boundary_message["seq"],
                }
            )
            return True
        except Exception as exc:
            if self._should_degrade(context_config):
                logger.warning("Update workflow context failed, ignored: %s", exc)
                return False
            raise
