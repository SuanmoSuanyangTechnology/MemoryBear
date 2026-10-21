"""Prompt-optimizer service (port of the host ``PromptOptimizerService``).

Management-plane CRUD (session create, history read, releases) runs
synchronously against a sync session, matching the host routes. The streaming
optimization runs on the runtime plane: it receives an ``AsyncSession`` and
performs every database touch through it — no sync session inside the event
loop.

Error strings and the streaming tag protocol are kept verbatim from the host
for behavioural parity during the coexistence period (including the host's
"Pompt template not found" typo).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import langid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..model_clients.chat import RedBearChatModel
from ..models.prompt_optimizer_model import PromptOptimizerSession, RoleType
from ..repositories.prompt_optimizer_repository import (
    PromptOptimizerSessionRepository,
    PromptReleaseRepository,
)
from ..utils.datetime_utils import to_timestamp_ms
from .model_service import ModelConfigService as ModelSvc
from .prompt import prompt_manager

logger = logging.getLogger(__name__)


class PromptOptimizerService:
    def __init__(self, db: Session | AsyncSession):
        self.db = db
        self.optim_repo = PromptOptimizerSessionRepository(db)
        self.release_repo = PromptReleaseRepository(db)

    # ==================== Management plane (sync) ====================

    def create_session(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> PromptOptimizerSession:
        session = self.optim_repo.create_session(tenant_id=tenant_id, user_id=user_id)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_session_message_history(
        self, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[tuple[str, str]]:
        history = self.optim_repo.get_session_history(
            session_id=session_id, user_id=user_id
        )
        return [(message.role, message.content) for message in history]

    def save_prompt(
        self,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        title: str,
        prompt: str,
    ) -> dict:
        session = self.optim_repo.get_session_by_id(session_id)
        if session is None or session.tenant_id != tenant_id:
            raise BusinessException(
                "Session does not exist or the current user has no access",
                BizCode.BAD_REQUEST,
            )

        if self.release_repo.get_prompt_by_session_id(session_id):
            raise BusinessException(
                "A release already exists for the current session",
                BizCode.BAD_REQUEST,
            )

        prompt_obj = self.release_repo.create_prompt_release(
            tenant_id=tenant_id,
            title=title,
            session_id=session_id,
            prompt=prompt,
        )
        self.db.commit()
        self.db.refresh(prompt_obj)
        return {
            "id": prompt_obj.id,
            "session_id": prompt_obj.session_id,
            "title": prompt_obj.title,
            "prompt": prompt_obj.prompt,
            "created_at": to_timestamp_ms(prompt_obj.created_at),
        }

    def delete_prompt(self, tenant_id: uuid.UUID, prompt_id: uuid.UUID) -> None:
        prompt_obj = self.release_repo.get_prompt_by_id(prompt_id)
        if not prompt_obj or prompt_obj.is_delete:
            raise BusinessException(
                "Prompt does not exist or has already been deleted",
                BizCode.NOT_FOUND,
            )

        if prompt_obj.tenant_id != tenant_id:
            raise BusinessException(
                "No permission to delete this prompt",
                BizCode.FORBIDDEN,
            )

        self.release_repo.soft_delete_prompt(prompt_obj)
        self.db.commit()
        logger.info(
            "Prompt soft deleted, prompt_id=%s, tenant_id=%s", prompt_id, tenant_id
        )

    def get_release_list(
        self,
        tenant_id: uuid.UUID,
        page: int,
        page_size: int,
        filter_keyword: str | None = None,
    ) -> dict[str, Any]:
        offset = (page - 1) * page_size

        if filter_keyword:
            total = self.release_repo.count_prompts_by_keyword(tenant_id, filter_keyword)
            releases = self.release_repo.search_prompts_paginated(
                tenant_id=tenant_id,
                keyword=filter_keyword,
                offset=offset,
                limit=page_size,
            )
        else:
            total = self.release_repo.count_prompts(tenant_id)
            releases = self.release_repo.get_prompts_paginated(
                tenant_id=tenant_id,
                offset=offset,
                limit=page_size,
            )

        # One batched lookup instead of the host's per-release query.
        first_messages = self.optim_repo.get_first_user_messages(
            [release.session_id for release in releases]
        )
        items = [
            {
                "id": release.id,
                "title": release.title,
                "prompt": release.prompt,
                "created_at": to_timestamp_ms(release.created_at),
                "first_message": first_messages.get(release.session_id),
            }
            for release in releases
        ]

        log_msg = (
            f"Retrieved {len(items)} prompt releases, page={page}, tenant_id={tenant_id}"
        )
        if filter_keyword:
            log_msg += f", filter='{filter_keyword}'"
        logger.info(log_msg)

        return {
            "page": {
                "total": total,
                "page": page,
                "pagesize": page_size,
                "hasnext": page * page_size < total,
            },
            "keyword": filter_keyword,
            "items": items,
        }

    # ==================== Runtime plane (async) ====================

    async def _create_message_async(
        self,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        role: RoleType,
        content: str,
    ) -> None:
        await self.optim_repo.create_message_async(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content,
        )
        await self.db.commit()

    async def _get_session_message_history_async(
        self, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[tuple[str, str]]:
        history = await self.optim_repo.get_session_history_async(
            session_id=session_id, user_id=user_id
        )
        return [(message.role, message.content) for message in history]

    async def optimize_prompt(
        self,
        tenant_id: uuid.UUID,
        model_id: uuid.UUID,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        current_prompt: str,
        user_require: str,
        skill: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream the optimized prompt and yield the final desc/variables chunk."""

        await self._create_message_async(
            tenant_id, session_id, user_id, role=RoleType.USER, content=user_require
        )
        model_config = await ModelSvc.get_model_by_id_async(
            self.db, model_id, tenant_id=tenant_id
        )
        session_history = await self._get_session_message_history_async(
            session_id=session_id, user_id=user_id
        )

        logger.info(
            "Prompt optimization started, user_id=%s, session_id=%s", user_id, session_id
        )

        # Remote invoke seam: the view carries only the config id and tenant,
        # credentials stay in the model service.
        view = await ModelSvc.get_runtime_model_view_async(
            self.db, model_config.id, tenant_id=tenant_id
        )
        llm = RedBearChatModel.for_invoke(view, streaming=True)
        try:
            rendered_system_message = prompt_manager.render(
                "prompt_optimizer_system",
                skill=skill,
                language=langid.classify(user_require)[0],
            )
            rendered_user_message = prompt_manager.render(
                "prompt_optimizer_user",
                current_prompt=current_prompt,
                user_require=user_require,
            )
        except FileNotFoundError:
            logger.error("Prompt optimizer not found.", exc_info=True)
            raise BusinessException(
                message="Pompt template not found", code=BizCode.NOT_FOUND
            ) from None
        except Exception as e:
            logger.error("Failed to load prompt template.", exc_info=True)
            raise BusinessException(
                message="Internal server error", code=BizCode.INTERNAL_ERROR
            ) from e

        messages = [
            (
                RoleType.SYSTEM.value,
                rendered_system_message,
            ),
        ]

        messages.extend(session_history[:-1])  # last message is current message
        messages.extend([(RoleType.USER.value, rendered_user_message)])
        buffer = ""
        prompt_started = False
        prompt_finished = False
        idx = 0

        START_PROMPT_TAG = "<START_PROMPT>"
        END_PROMPT_TAG = "<END_PROMPT>"
        START_DESC_TAG = "<START_DESC>"
        END_DESC_TAG = "<END_DESC>"

        async for chunk in llm.astream(messages):
            content = getattr(chunk, "content", chunk)
            if not content:
                continue
            if isinstance(content, str):
                buffer += content
            elif isinstance(content, list):
                for item in content:
                    buffer += item["text"]
            else:
                logger.error("Unsupported content type - %s", content)
                raise Exception("Unsupported content type")

            # Phase 1: Stream prompt content between tags
            if not prompt_finished:
                if not prompt_started:
                    if START_PROMPT_TAG in buffer:
                        prompt_started = True
                        idx = buffer.index(START_PROMPT_TAG) + len(START_PROMPT_TAG)
                        # Check if end tag already in buffer
                        if END_PROMPT_TAG in buffer[idx:]:
                            end = buffer.index(END_PROMPT_TAG, idx)
                            yield {"content": buffer[idx:end]}
                            prompt_finished = True
                        elif len(buffer) > idx:
                            yield {"content": buffer[idx:]}
                            idx = len(buffer)
                else:
                    if END_PROMPT_TAG in buffer[idx:]:
                        end = buffer.index(END_PROMPT_TAG, idx)
                        yield {"content": buffer[idx:end]}
                        prompt_finished = True
                    else:
                        # Hold back enough chars to avoid splitting the end tag
                        safe_end = len(buffer) - len(END_PROMPT_TAG)
                        if safe_end > idx:
                            yield {"content": buffer[idx:safe_end]}
                            idx = safe_end
            else:
                # Phase 2: Wait for desc end tag, then stop
                if END_DESC_TAG in buffer:
                    break

        # Extract prompt and desc from buffer using tags with strict validation
        prompt_text = ""
        desc_text = ""
        parse_errors = []

        # Validate prompt tags
        has_prompt_start = START_PROMPT_TAG in buffer
        has_prompt_end = END_PROMPT_TAG in buffer
        if has_prompt_start and has_prompt_end:
            p_start = buffer.index(START_PROMPT_TAG) + len(START_PROMPT_TAG)
            p_end = buffer.index(END_PROMPT_TAG)
            if p_start > p_end:
                parse_errors.append(
                    f"Malformed output: {END_PROMPT_TAG} appears before {START_PROMPT_TAG}"
                )
            else:
                prompt_text = buffer[p_start:p_end].strip()
        else:
            missing = []
            if not has_prompt_start:
                missing.append(START_PROMPT_TAG)
            if not has_prompt_end:
                missing.append(END_PROMPT_TAG)
            parse_errors.append(f"Missing prompt markers: {', '.join(missing)}")

        # Validate desc tags
        has_desc_start = START_DESC_TAG in buffer
        has_desc_end = END_DESC_TAG in buffer
        if has_desc_start and has_desc_end:
            d_start = buffer.index(START_DESC_TAG) + len(START_DESC_TAG)
            d_end = buffer.index(END_DESC_TAG)
            if d_start > d_end:
                parse_errors.append(
                    f"Malformed output: {END_DESC_TAG} appears before {START_DESC_TAG}"
                )
            else:
                desc_text = buffer[d_start:d_end].strip()
        elif has_desc_start and not has_desc_end:
            # Stream ended before <END_DESC> — likely max_tokens truncation
            # Use everything after <START_DESC> as desc (best effort)
            d_start = buffer.index(START_DESC_TAG) + len(START_DESC_TAG)
            desc_text = buffer[d_start:].strip()
            logger.warning(
                "Desc end marker missing (likely truncated), using content after %s as desc",
                START_DESC_TAG,
            )
        else:
            missing = []
            if not has_desc_start:
                missing.append(START_DESC_TAG)
            if not has_desc_end:
                missing.append(END_DESC_TAG)
            parse_errors.append(f"Missing desc markers: {', '.join(missing)}")

        # If prompt is missing, this is a hard failure — user got no streamed
        # content or garbage
        if not prompt_text:
            error_detail = "; ".join(parse_errors) if parse_errors else "Prompt content is empty"
            logger.error(
                "Prompt optimization output parsing failed: %s. "
                "Raw buffer (first 500 chars): %s",
                error_detail,
                buffer[:500],
            )
            raise BusinessException(
                message=f"优化结果解析失败: {error_detail}",
                code=BizCode.INTERNAL_ERROR,
            )

        # Desc missing is non-fatal but should be visible
        if not desc_text:
            warning_detail = "; ".join(parse_errors) if parse_errors else "Desc content is empty"
            logger.warning(
                "Prompt optimization desc parsing issue: %s. Falling back to default desc.",
                warning_detail,
            )
            desc_text = "Prompt optimized successfully."

        logger.info("Optimized prompt length: %d, desc: %s", len(prompt_text), desc_text)
        await self._create_message_async(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            role=RoleType.ASSISTANT,
            content=desc_text,
        )
        variables = self.parser_prompt_variables(prompt_text)
        logger.info(
            "Prompt optimization completed, user_id=%s, session_id=%s", user_id, session_id
        )
        yield {"desc": desc_text, "variables": variables}

    @staticmethod
    def parser_prompt_variables(prompt: str) -> list[str]:
        try:
            pattern = r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}"
            matches = re.findall(pattern, str(prompt))
            return list(set(matches))
        except Exception as e:
            logger.error("Failed to parse prompt variables - Error: %s", e, exc_info=True)
            raise BusinessException(
                "Failed to parse prompt variables", BizCode.PARSER_NOT_SUPPORTED
            ) from e


__all__ = ["PromptOptimizerService"]
