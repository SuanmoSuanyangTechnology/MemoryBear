"""Memory-storage use cases and legacy timeline presentation rules."""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from functools import partial
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import UUID

from neo4j.time import DateTime as Neo4jDateTime
from starlette.datastructures import Headers, UploadFile

from src.config import memory_output_override
from src.infrastructure.file_service import FileServiceError
from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.common.datetime_utils import parse_iso_to_utc_naive, to_timestamp_ms
from src.schemas.event_category_models import EVENT_CATEGORY_NAMES, EVENT_CATEGORY_NAME_TO_ID
from src.emotion.emotion_buckets import emotion_valence
from src.repositories.end_user import EndUserRepository
from src.repositories.memory_storage import MemoryStorageRepository
from src.repositories.profile import WorkspaceProfileRepository

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

from src.common.llm_error_utils import ClassifiedLLMError, classify_llm_error
from src.common.sse_utils import format_sse_message
from src.i18n import t
from src.services.multimodal_service import MultimodalService
from src.schemas.memory_storage import FileInput, FileType, ModelInfo, PilotRunInput, TransferMethod, TrialRunChatInput, WriteMessageItem
from src.schemas.memory_config import ConfigurationError, MemoryConfig
from src.schemas.message_models import DialogData
from src.bootstrap import get_settings

logger = logging.getLogger(__name__)

#: 用户名片 Tag 的字符长度上限（api ``user_card_tags.USER_CARD_TAG_MAX_LENGTH`` 同值）。
USER_CARD_TAG_MAX_LENGTH = 12

TRIAL_RUN_CHAT_SYSTEM_PROMPT = """
You are a neutral and helpful AI assistant in a trial-run conversation. Answer
general questions normally using your own general knowledge. Use the explicit
conversation history and the supplied attachment analysis results as additional
context when they are relevant. Attachment labels identify every current-message
attachment. Answer the user's question directly, but do not reproduce the complete
attachment analyses or add an attachment-details appendix; the backend appends the
original analyses after your answer. If an attachment could not be understood, say
so instead of silently omitting it. Do not claim to have accessed long-term memory,
a private knowledge base, tools, or skills. If a question requires unavailable
private or up-to-date information, state that limitation clearly. Reply in the
language used by the user; when it is unclear, use the requested interface language.
""".strip()
TRIAL_RUN_ATTACHMENT_ANALYSIS_PROMPT = """
Analyze every supplied attachment independently and return detailed plain-text
descriptions. Preserve each attachment label exactly. For images, cover visible
objects, text, layout, and relevant context. For audio, cover speech, speakers,
important sounds, and sequence. For video, cover scenes, actions, visible text,
speech or audio, and the timeline. Do not answer the user's overall question and do
not omit an attachment. If an attachment cannot be understood, state the failure
under that attachment label.
""".strip()


class TimelineSort(Enum):
    """记忆时间线排序枚举。

    成员值即对外 API 的 sort 参数字符串。
    - time_*: 全部按 created_at 排序（key_node=发生时间，其他=记录时间）
    - occurred_*: 仅 key_node 按发生时间排序，其余无发生时间排最后
    - recorded_*: 仅 statement/memory_summary 按记录时间排序，key_node 无记录时间排最后
    """

    TIME_ASC = "time_asc"
    TIME_DESC = "time_desc"
    OCCURRED_ASC = "occurred_asc"
    OCCURRED_DESC = "occurred_desc"
    RECORDED_ASC = "recorded_asc"
    RECORDED_DESC = "recorded_desc"

    @property
    def sort_mode(self) -> str:
        """排序字段模式：time / occurred / recorded。"""
        return self.value.rsplit("_", 1)[0]

    @property
    def is_asc(self) -> bool:
        """是否升序。"""
        return self.value.endswith("_asc")

    @classmethod
    def values(cls) -> set:
        """返回所有合法 sort 字符串集合，供校验使用。"""
        return {member.value for member in cls}


class MemoryStorageService:
    def __init__(self, runtime: ProfileRuntime):
        self.runtime = runtime
        self.repository = MemoryStorageRepository(
            runtime.neo4j, runtime.postgres, runtime.elasticsearch
        )
        self.end_users = EndUserRepository(runtime.postgres)
        self.workspace_profile = WorkspaceProfileRepository(runtime.postgres)

    async def _owner(self, element_id: str, workspace_id: UUID) -> str:
        owner = await self.repository.owner(element_id)
        try:
            user_id = UUID(str(owner))
        except ValueError as exc:
            raise ProfileError(
                BizCode.PERMISSION_DENIED, "无法验证节点的用户归属", status_code=200
            ) from exc
        user = await self.end_users.resolve_user_in_workspace(user_id, workspace_id)
        if user is None or str(user["id"]) != str(user_id):
            raise ProfileError(
                BizCode.PERMISSION_DENIED, "该节点不属于当前工作空间", status_code=200
            )
        return str(user_id)

    async def get_workspace_models_configs_async(self, workspace_id):
        from src.repositories.workspace import WorkspaceRepository

        return await WorkspaceRepository(self.runtime.postgres).workspace_row(workspace_id)

    async def workspace_hot_memory_tags(
        self, workspace_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        """工作空间热门记忆标签：``end_users.memory_tags`` 跨用户精确匹配合并。

        ``limit <= 0`` 时按 api 语义回落 10（api 的请求参数没有上下界校验）。
        """

        effective_limit = limit if limit > 0 else 10
        try:
            tags = await self.workspace_profile.hot_memory_tags(
                workspace_id, effective_limit, USER_CARD_TAG_MAX_LENGTH
            )
        except Exception as exc:
            logger.error(
                "hot memory tags failed: workspace_id=%s error=%s",
                workspace_id,
                type(exc).__name__,
                exc_info=True,
            )
            raise LocalizedProfileError(
                BizCode.INTERNAL_ERROR,
                "analytics.error.hot_memory_tags_failed",
                detail=str(exc),
                status_code=500,
            ) from exc
        return [{"name": name, "frequency": frequency} for name, frequency in tags]

    async def get_timeline_memories_server(self, id, label, model_id, language_type, *, workspace_id):
        """
        获取时间线记忆数据

        Args:
            id: 节点ID
            table: 节点类型/标签

        Returns:
            Dict包含：
            - success: 是否成功
            - data: 时间线数据列表
            - total: 数据总数
            - error: 错误信息（如果有）

        根据不同标签返回相应字段：
        - MemorySummary: content字段
        - Statement: statement字段
        - ExtractedEntity: name字段
        """
        await self._owner(id, workspace_id)
        try:
            logger.info(f"获取时间线记忆数据 - ID: {id}, Table: {label}")

            # Query selection and execution belong to the repository.
            results = await self.repository.timeline(id, label)

            # 记录查询结果的类型和内容用于调试
            logger.info(f"时间线查询结果类型: {type(results)}, 长度: {len(results) if isinstance(results, list) else 'N/A'}")
            
            # 处理查询结果
            timeline_data =await self._process_timeline_results(results, model_id, language_type)

            logger.info(f"成功获取时间线记忆数据: 总计 {len(timeline_data.get('timelines_memory', []))} 条")

            return timeline_data
            
        except Exception as e:
            logger.error(f"获取时间线记忆数据失败: {str(e)}", exc_info=True)
            return  str(e)

    async def get_end_user_id_by_element_id(self, element_id):
        return await self.repository.owner(element_id)

    async def get_unified_timeline(
        self,
        id,
        source_type: str = "all",
        page: int = 1,
        pagesize: int = 10,
        sort: str = "time_desc",
        from_ts: int = None,
        to_ts: int = None,
        end_user_id: str | None = None,
    ) -> dict:
        """ExtractedEntity 合并时间线（关键节点 / 来源陈述 / 长期沉淀），分页 + 按来源筛选 + 排序 + 日期过滤。

        本方法仅服务 ExtractedEntity 节点：
        - 关键节点：读自身 event_timeline 结构化事件。created_at = 发生时间（valid_at）。
        - 来源陈述 / 长期沉淀：走图遍历查询，取关联 Statement / MemorySummary。created_at = 记录时间。

        排序规则（基于 created_at，不同类型语义不同）：
        - time_asc/desc: 全部按 created_at 排序
        - occurred_asc/desc: key_node 按 created_at（发生时间），其他为 null → 永远最后
        - recorded_asc/desc: statement/memory_summary 按 created_at（记录时间），key_node 为 null → 永远最后

        日期过滤：from_ts / to_ts 为 UTC 毫秒时间戳，由前端传入，后端直接数值比较。

        total_count / type_stats 始终基于全量，不受筛选影响。
        """
        repository = self.repository
        element_id = id
        if element_id is None and end_user_id is not None:
            entities = await repository.user_entities(end_user_id)
            if len(entities) > 1:
                raise ProfileError(BizCode.INTERNAL_ERROR, "用户自身实体定位不唯一", status_code=200)
            element_id = entities[0]["element_id"] if entities else None

        # 解析排序参数
        sort_mode, sort_asc = self._parse_sort(sort)

        # 1. 关键节点：读 ExtractedEntity 自身 event_timeline
        entity_name = None
        entity_type = None
        description_summary = None
        key_node_items = []
        kn_results = await self.repository.events(element_id)
        if kn_results:
            record = kn_results[0]
            entity_name = record.get("entity_name")
            entity_type = record.get("entity_type")
            description_summary = record.get("description_summary")
            events = self._parse_event_timeline(record.get("event_timeline") or "")
            key_node_items = [
                {
                    "type": "key_node",
                    "category": EVENT_CATEGORY_NAME_TO_ID.get(e.get("category")),
                    "title": e.get("title"),
                    "text": e.get("fact"),
                    "created_at": e.get("valid_at"),
                    "timetype": "occurred",
                }
                for e in events
            ]

        # 2. 来源陈述 + 长期沉淀：走 ExtractedEntity 图遍历查询
        graph_results = await self.repository.timeline(element_id, "ExtractedEntity")

        statement_items, summary_items = [], []
        if graph_results and isinstance(graph_results, list):
            for record in graph_results:
                if not isinstance(record, dict):
                    continue
                statement_items.extend(
                    self._collect_graph_source_items(record.get("statement"), "statement")
                )
                summary_items.extend(
                    self._collect_graph_source_items(record.get("MemorySummary"), "memory_summary")
                )
            # 跨 record 再按 text 去重
            statement_items = self._dedup_by_text(statement_items)
            summary_items = self._dedup_by_text(summary_items)

        # 3. type_stats（全量，不受筛选影响）；total_count 单独拎出
        total_count = len(key_node_items) + len(statement_items) + len(summary_items)
        type_stats = [
            {"type": "key_node", "count": len(key_node_items)},
            {"type": "statement", "count": len(statement_items)},
            {"type": "memory_summary", "count": len(summary_items)},
        ]

        # 4. 合并
        all_items = key_node_items + statement_items + summary_items

        # 5. 日期过滤（from_date / to_date 共用，不同类型用不同时间字段）
        if from_ts is not None or to_ts is not None:
            all_items = [i for i in all_items if self._match_date_filter(i, from_ts, to_ts)]

        filtered_counts = {
            "key_node": sum(item["type"] == "key_node" for item in all_items),
            "memory_summary": sum(item["type"] == "memory_summary" for item in all_items),
        }
        filtered_counts["total"] = sum(filtered_counts.values())

        # 6. 排序
        all_items.sort(key=lambda x: self._get_sort_key(x, sort_mode, sort_asc))

        # 7. type 筛选
        if source_type != "all":
            filtered = [i for i in all_items if i["type"] == source_type]
        else:
            filtered = all_items

        # 8. 分页
        total = len(filtered)
        start = (page - 1) * pagesize
        items = filtered[start:start + pagesize]
        hasnext = page * pagesize < total

        logger.info(
            f"合并时间线: id={element_id}, type={source_type}, sort={sort}, "
            f"from_ts={from_ts}, to_ts={to_ts}, "
            f"key_node={len(key_node_items)}, statement={len(statement_items)}, "
            f"summary={len(summary_items)}, total={total}, page={page}, pagesize={pagesize}"
        )

        return {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "description_summary": description_summary,
            "total_count": total_count,
            "type_stats": type_stats,
            "filtered_counts": filtered_counts,
            "items": items,
            "page": {"page": page, "pagesize": pagesize, "total": total, "hasnext": hasnext},
        }

    @staticmethod
    def _to_epoch_ms(value: str):
        """把事件日期转为 Unix 毫秒时间戳（UTC）。

        底层 event_timeline 只存日期（YYYY-MM-DD），统一走 datetime_utils 解析为
        naive UTC 再序列化为毫秒时间戳；无法解析则返回 None（调用方据此过滤）。
        """
        if not value:
            return None
        try:
            dt = parse_iso_to_utc_naive(value.strip())
        except ValueError:
            return None
        return to_timestamp_ms(dt)

    @staticmethod
    def _parse_event_timeline(event_timeline: str) -> list:
        """解析 event_timeline 字符串为结构化事件列表，按 valid_at 降序排列

        只展示新格式（4 段正文：fact|title|category|category_id）；旧格式（1 段，仅 fact）
        字段残缺，直接跳过不返回。数据库中的 "NULL" 字符串统一转为 None；时间为空的事件不返回。

        Args:
            event_timeline: 原始 event_timeline 字符串

        Returns:
            结构化事件列表，按 valid_at 降序
        """
        if not event_timeline or not event_timeline.strip():
            return []

        events = []
        for item in event_timeline.split('；'):
            item = item.strip()
            if not item:
                continue

            # 抠出 [valid_at|invalid_at]
            m = re.match(r'^\[([^|]*)\|([^\]]*)\]\s*(.*)', item)
            if m:
                valid_at = m.group(1).strip() or None
                invalid_at = m.group(2).strip() or None
                body = m.group(3)
            else:
                valid_at = None
                invalid_at = None
                body = item

            # "NULL" → None
            if valid_at == "NULL":
                valid_at = None
            if invalid_at == "NULL":
                invalid_at = None

            # 按 | 切正文段（新格式为 4 段：fact|title|category|category_id）
            parts = body.split('|')

            # 旧格式（仅 fact，1 段）字段残缺，不展示给前端
            if len(parts) < 4:
                continue

            fact = parts[0].strip()
            title = parts[1].strip()
            category = parts[2].strip()
            category_id = parts[3].strip()

            # "NULL" → None
            if title == "NULL":
                title = None
            if category == "NULL":
                category = None
            if category_id == "NULL":
                category_id = None

            # fact 为空则跳过
            if not fact:
                continue

            # valid_at 为空时用 invalid_at 兜底（事件有结束日期但开始不确定）
            display_at = valid_at or invalid_at
            if not display_at:
                continue

            # 转为 Unix 毫秒时间戳（UTC）；无法解析则跳过
            display_at_ms = MemoryStorageService._to_epoch_ms(display_at)
            if display_at_ms is None:
                continue

            # 不返回 invalid_at / category_id（前端不需要）
            events.append({
                "title": title,
                "fact": fact,
                "category": category,
                "valid_at": display_at_ms,
            })

        # 排序：valid_at 降序（最新在前）
        events.sort(key=lambda e: e["valid_at"], reverse=True)
        return events

    def _collect_graph_source_items(self, raw_value, source_type: str) -> list:
        """把图遍历返回的 statement / MemorySummary 字段转为统一 item（created_at 毫秒）。

        raw_value 可能是 collect 列表（ExtractedEntity / MemorySummary 查询）或单个对象
        （Statement 查询的 statement 字段）；CASE 未匹配会产生 None，需过滤。
        时间无法解析（created_at 为 None）的记录直接丢弃，保证每条都有有效时间。

        created_at 即为记录时间；category / timetype 按来源类型设置。
        """
        items = []
        candidates = raw_value if isinstance(raw_value, list) else [raw_value]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            text = c.get("text")
            if text is None or str(text).strip() == "" or "MemorySummaryChunk" in str(text):
                continue
            created_at = self._neo4j_dt_to_ms(c.get("created_at"))
            if created_at is None:
                continue

            category = "source_statement" if source_type == "statement" else "long_term_memory"

            items.append({
                "type": source_type,
                "category": category,
                "title": None,
                "text": text,
                "created_at": created_at,
                "timetype": "recording",
            })
        return items

    @staticmethod
    def _parse_sort(sort: str) -> tuple:
        """解析排序参数，返回 (sort_mode, sort_asc)。

        sort_mode: "time" | "occurred" | "recorded"
        sort_asc: True 表示升序，False 表示降序

        非法值直接抛 ValueError，不再静默回退默认值；HTTP 层（controller）
        已用 ``TimelineSort.values()`` 做同样校验，此处保持行为一致。
        """
        try:
            member = TimelineSort(sort)
        except ValueError:
            raise ValueError(f"非法的 sort 值: {sort}")
        return member.sort_mode, member.is_asc

    @staticmethod
    def _get_sort_key(item: dict, sort_mode: str, sort_asc: bool) -> tuple:
        """生成排序键：(is_null, timestamp)。

        created_at 在不同类型中的语义不同：
        - key_node: created_at = 发生时间
        - statement / memory_summary: created_at = 记录时间

        sort_mode="time": 全部按 created_at 排序（key_node=发生, 其他=记录）
        sort_mode="occurred": key_node 有发生时间（created_at），其他为 null → 永远最后
        sort_mode="recorded": statement/memory_summary 有记录时间（created_at），key_node 为 null → 永远最后

        null 的 is_null=1 保证永远排最后；时间戳按方向取值，升序用原值，降序取负值。
        """
        if sort_mode == "occurred":
            # key_node 的 created_at 就是发生时间；其他类型没有发生时间
            ts = item["created_at"] if item["type"] == "key_node" else None
        elif sort_mode == "recorded":
            # statement / memory_summary 的 created_at 就是记录时间；key_node 没有记录时间
            ts = item["created_at"] if item["type"] != "key_node" else None
        else:  # "time" — 全部按 created_at
            ts = item.get("created_at")

        is_null = 1 if ts is None else 0
        if ts is None:
            ts = 0
        # 降序时取负值，这样 Python 默认升序排列后实际是降序
        if not sort_asc:
            ts = -ts
        return (is_null, ts)

    @staticmethod
    def _match_date_filter(item: dict, from_ts: int | None, to_ts: int | None) -> bool:
        """判断 item.created_at 是否在 [from_ts, to_ts] 范围内。

        from_ts / to_ts 为 UTC 毫秒时间戳，由前端传入，后端直接数值比较。
        """
        ts = item.get("created_at")
        if ts is None:
            return False
        if from_ts is not None and ts < from_ts:
            return False
        if to_ts is not None and ts > to_ts:
            return False
        return True

    @staticmethod
    def _dedup_by_text(items: list) -> list:
        """按 text 去重，保留首次出现（保留其 created_at）。"""
        seen = set()
        result = []
        for item in items:
            text = item.get("text")
            if text in seen:
                continue
            seen.add(text)
            result.append(item)
        return result

    @staticmethod
    def _neo4j_dt_to_ms(dt):
        """Neo4j DateTime / ISO 字符串 → Unix 毫秒时间戳（UTC）；无法解析返回 None。

        统一走项目 datetime_utils：先取 ISO 字符串，parse 为 naive UTC，再转毫秒。
        """
        if dt is None:
            return None
        try:
            if hasattr(dt, "iso_format"):
                iso = dt.iso_format()
            elif isinstance(dt, str):
                iso = dt
            else:
                iso = str(dt)
            return to_timestamp_ms(parse_iso_to_utc_naive(iso))
        except Exception:
            return None

    async def _process_timeline_results(self, results: List[Dict[str, Any]], model_id: str, language_type: str) -> Dict[str, Any]:
        """
        处理时间线查询结果
        
        Args:
            results: Neo4j查询结果
            model_id: 模型ID用于翻译
            language_type: 语言类型 ('zh' 或其他)
            
        Returns:
            处理后的时间线数据字典
        """
        # 检查results是否为空或不是列表
        if not results or not isinstance(results, list):
            logger.warning(f"时间线查询结果为空或格式不正确: {type(results)}")
            return {
                "MemorySummary": [],
                "Statement": [],
                "ExtractedEntity": [],
                "timelines_memory": []
            }
        
        memory_summary_list = []
        statement_list = []
        extracted_entity_list = []
        
        for data in results:
            # 检查data是否为字典类型
            if not isinstance(data, dict):
                logger.warning(f"跳过非字典类型的记录: {type(data)} - {data}")
                continue
            
            # 处理MemorySummary
            summary = data.get('MemorySummary')
            if summary is not None:
                processed_summary = await self._process_field_value(summary, "MemorySummary")
                memory_summary_list.extend(processed_summary)
            
            # 处理Statement
            statement = data.get('statement')
            if statement is not None:
                processed_statement = await self._process_field_value(statement, "Statement")
                statement_list.extend(processed_statement)
            
            # 处理ExtractedEntity
            extracted_entity = data.get('ExtractedEntity')
            if extracted_entity is not None:
                processed_entity = await self._process_field_value(extracted_entity, "ExtractedEntity")
                extracted_entity_list.extend(processed_entity)
        
        # 去重 - 现在处理的是字典列表，需要更智能的去重
        memory_summary_list = self._deduplicate_dict_list(memory_summary_list)
        statement_list = self._deduplicate_dict_list(statement_list)
        extracted_entity_list = self._deduplicate_dict_list(extracted_entity_list)
        
        # 合并所有数据并处理相同text的合并
        all_timeline_data = memory_summary_list + statement_list
        all_timeline_data = self._merge_same_text_items(all_timeline_data)
        
        # 如果需要翻译（非中文），对整个结果进行翻译

        result = {
            "MemorySummary": memory_summary_list,
            "Statement": statement_list,
            "ExtractedEntity": extracted_entity_list,
            "timelines_memory": all_timeline_data
        }
        
        logger.info(f"时间线数据处理完成: MemorySummary={len(memory_summary_list)}, Statement={len(statement_list)}, ExtractedEntity={len(extracted_entity_list)}")
        
        return result

    def _deduplicate_dict_list(self, dict_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        对字典列表进行去重
        
        Args:
            dict_list: 字典列表
            
        Returns:
            去重后的字典列表
        """
        seen = set()
        result = []
        
        for item in dict_list:
            # 使用text作为去重的键
            text = item.get('text', '')
            if text and text not in seen:
                seen.add(text)
                result.append(item)
        
        return result

    def _merge_same_text_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        合并具有相同text的项目，合并type字段，保留一个时间
        
        Args:
            items: 项目列表
            
        Returns:
            合并后的项目列表
        """
        text_groups = {}
        
        # 按text分组
        for item in items:
            text = item.get('text', '')
            if not text:
                continue
                
            if text not in text_groups:
                text_groups[text] = {
                    'text': text,
                    'types': set(),
                    'created_at': item.get('created_at'),
                    'latest_time': item.get('created_at')
                }
            
            # 添加type到集合中
            item_type = item.get('type')
            if item_type:
                text_groups[text]['types'].add(item_type)
            
            # 保留最新的时间（如果有的话）
            current_time = item.get('created_at')
            if current_time and (not text_groups[text]['latest_time'] or 
                               self._is_later_time(current_time, text_groups[text]['latest_time'])):
                text_groups[text]['latest_time'] = current_time
        
        # 转换为最终格式
        result = []
        for text, group_data in text_groups.items():
            merged_item = {
                'text': text,
                'type': ', '.join(sorted(group_data['types'])),  # 合并多个type
                'created_at': group_data['latest_time']
            }
            result.append(merged_item)
        
        # 按时间排序（最新的在前）
        result.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return result

    def _is_later_time(self, time1: str, time2: str) -> bool:
        """
        比较两个时间字符串，判断time1是否晚于time2
        
        Args:
            time1: 时间字符串1
            time2: 时间字符串2
            
        Returns:
            time1是否晚于time2
        """
        try:
            if not time1 or not time2:
                return bool(time1)  # 如果time2为空，time1存在就算更晚
            
            # 简单的字符串比较（适用于ISO格式的时间）
            return time1 > time2
        except Exception:
            return False

    async def _process_field_value(self, value: Any, field_name: str) -> List[Dict[str, Any]]:
        """
        处理字段值，支持字符串、列表等类型
        
        Args:
            value: 字段值
            field_name: 字段名称（用于日志）
            
        Returns:
            处理后的字典列表
        """
        processed_values = []

        try:
            if isinstance(value, list):
                # 如果是列表，处理每个元素
                for item in value:
                    if self._is_valid_item(item):
                        processed_item = await self._process_single_item(item)
                        if processed_item:
                            processed_values.append(processed_item)
            elif isinstance(value, dict):
                # 如果是字典，直接处理
                if self._is_valid_item(value):
                    processed_item = await self._process_single_item(value)
                    if processed_item:
                        processed_values.append(processed_item)
            elif isinstance(value, str):
                # 如果是字符串，转换为字典格式
                if value.strip() != '' and "MemorySummaryChunk" not in value:
                    processed_values.append({
                        'text': value,
                        'type': field_name,
                        'created_at': None
                    })
            elif value is not None:
                # 其他类型转换为字符串
                str_value = str(value)
                if str_value.strip() != '' and "MemorySummaryChunk" not in str_value:
                    processed_values.append({
                        'text': str_value,
                        'type': field_name,
                        'created_at': None
                    })
        except Exception as e:
            logger.warning(f"处理字段 {field_name} 的值时出错: {e}, 值类型: {type(value)}, 值: {value}")
        
        return processed_values

    def _is_valid_item(self, item: Any) -> bool:
        """
        检查项目是否有效
        
        Args:
            item: 要检查的项目
            
        Returns:
            是否有效
        """
        if item is None:
            return False
            
        if isinstance(item, dict):
            text = item.get('text')
            return (text is not None and 
                   str(text).strip() != '' and 
                   "MemorySummaryChunk" not in str(text))
        
        return (str(item).strip() != '' and 
               "MemorySummaryChunk" not in str(item))

    async def _process_single_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        处理单个项目
        
        Args:
            item: 要处理的项目字典
            
        Returns:
            处理后的项目字典
        """
        try:
            text = item.get('text')
            created_at = item.get('created_at')
            item_type = item.get('type', '未知类型')
            
            # 转换Neo4j时间格式
            formatted_time = self._convert_neo4j_datetime(created_at)
            
            return {
                'text': text,
                'type': item_type,
                'created_at': formatted_time
            }
        except Exception as e:
            logger.warning(f"处理单个项目时出错: {e}, 项目: {item}")
            return None

    def _convert_neo4j_datetime(self, dt: Any) -> str:
        """
        转换Neo4j时间格式为标准时间字符串
        
        Args:
            dt: Neo4j时间对象或其他时间格式
            
        Returns:
            格式化的时间字符串
        """
        if dt is None:
            return None
            
        try:
            # 处理Neo4j DateTime对象
            if isinstance(dt, Neo4jDateTime):
                return dt.iso_format().replace('T', ' ').split('.')[0]
            
            # 处理其他neo4j时间类型
            if hasattr(dt, 'iso_format'):
                return dt.iso_format().replace('T', ' ').split('.')[0]
            
            # 处理字符串格式的时间
            if isinstance(dt, str):
                # 尝试解析ISO格式
                try:
                    parsed_dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
                    return parsed_dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return dt
            
            # 其他情况直接转换为字符串
            return str(dt)
            
        except Exception as e:
            logger.warning(f"转换时间格式失败: {e}, 原始值: {dt}")
            return str(dt) if dt is not None else None

    async def require_memory_owner(self, id, workspace_id):
        return await self._owner(id, workspace_id)

    async def get_emotion(self, id, label, model_id: str = None, language_type: str = 'zh') -> Dict[str, Any]:
        """
        获取情绪随时间变化数据
        
        Args:
            model_id: 模型ID用于翻译
            language_type: 语言类型 ('zh' 或其他)
        
        Returns:
            包含情绪数据的字典
        """
        try:
            logger.info(f"获取情绪数据 - ID: {id}, Table: {label}, language_type={language_type}")

            if label == 'Statement':
                results = await self.repository.emotions(id, "Statement")
            elif label == 'ExtractedEntity':
                results = await self.repository.emotions(id, "ExtractedEntity")
            else:
                # MemorySummary/Chunk类型查询
                results = await self.repository.emotions(id, label)

            # 处理查询结果
            emotion_data = self._process_emotion_results(results)
            
            # 转换Neo4j类型
            final_data = self._convert_emotion_types(emotion_data)
            
            # 如果需要翻译（非中文）
            if language_type != 'zh' and model_id and final_data:
                final_data = await self._translate_emotion_data(final_data, model_id)
            
            logger.info(f"成功获取 {len(final_data)} 条情绪数据")
            
            return final_data
            
        except Exception as e:
            logger.error(f"获取情绪数据失败: {str(e)}")
            return e

    async def get_interaction_frequency(self, id, label) -> Dict[str, Any]:
        """
        获取交互频率数据
        
        Returns:
            包含交互数据的字典
        """
        try:
            logger.info(f"获取交互数据 - ID: {id}, Table: {label}")
            ori_data= await self.repository.interaction_entity(id)
            if ori_data!=[]:
                # name = ori_data[0]['name']
                end_user_id = [i['end_user_id'] for i in ori_data][0]
                Space_User = await self.repository.interaction_user(end_user_id)
                if not Space_User:
                    return []
                user_id=Space_User[0]['id']
                results = await self.repository.interaction_associative(id, user_id)



                # 处理查询结果
                interaction_data = self._process_interaction_results(results)

                # 转换Neo4j类型
                final_data = self._convert_interaction_types(interaction_data)

                logger.info(f"成功获取 {len(final_data)} 条交互数据")

                return final_data
            return []
            
        except Exception as e:
            logger.error(f"获取交互数据失败: {str(e)}")
            return e

    async def resolve_trial_config_id(self, config_id, workspace_id):
        from src.repositories.memory_config import MemoryConfigReadRepository

        return await MemoryConfigReadRepository(self.runtime.postgres).resolve_trial_config_id(
            config_id, workspace_id
        )

    async def _trial_config(self, config_id, workspace_id, tenant_id, *, include_runtime_views=True):
        from src.services.memory_config.service import load_memory_config_async

        return await load_memory_config_async(
            self.runtime, config_id, workspace_id, tenant_id, include_runtime_views=include_runtime_views
        )

    async def pilot_run_stream(
        self, payload: PilotRunInput, language: str = "zh", *, workspace_id, tenant_id,
        actor_id: UUID, actor_kind: str,
    ) -> AsyncGenerator[str, None]:
        """
        流式执行试运行，产生 SSE 格式的进度事件

        db session 生命周期策略：
        - 阶段1（短 session）：用 get_db_read() 查配置、初始化 llm_client，with 块结束立即归还连接
        - 阶段2（无 session）：LLM 调用可能耗时数十秒，完全不持有 db 连接
        - 本方法不依赖 FastAPI Depends(get_db)，自行管理 session 生命周期

        Args:
            payload: 试运行配置和 QA 格式消息列表（v0.3.13：config_id + messages）
            language: 语言类型 ("zh" 中文, "en" 英文)，默认中文

        Yields:
            SSE 格式的字符串，包含以下事件类型：
            - 各种阶段名称: 进度更新 (如 starting, knowledge_extraction_complete 等)
            - result: 最终结果
            - error: 错误信息
            - done: 完成标记

        Raises:
            ValueError: 当配置无效或参数缺失时
            RuntimeError: 当管线执行失败时
        """
        from src.bootstrap import get_settings

        try:
            # 发出初始进度事件
            yield format_sse_message("starting", {
                "message": "开始试运行...",
                "time": int(time.time() * 1000)
            })

            # ── 阶段1：短 session，只读查询，with 块结束立即归还连接 ──────────
            payload_cid = str(getattr(payload, "config_id", "") or "").strip()
            cid: Optional[str] = payload_cid if payload_cid else None

            if not cid:
                raise ValueError("未提供 payload.config_id，禁止启动试运行")

            # v0.3.13: 直接透传 messages，不再解析 dialogue_text / custom_text
            messages = payload.messages
            if not messages:
                raise ValueError("试运行模式必须提供至少一条 message")

            # Load configuration through service-owned repository sessions.
            # 1a. 加载记忆配置
            try:
                memory_config, views = await self._trial_config(cid, workspace_id, tenant_id)
                logger.info(f"Configuration loaded successfully: {memory_config.config_name}")
            except ConfigurationError as e:
                raise RuntimeError(f"Configuration loading failed: {e}")

            logger.info(f"[PILOT_RUN_STREAM] messages count={len(messages)}")

            # 1c. 初始化 LLM 客户端（只需查一次模型配置，之后 llm_client 是独立对象）
            try:
                llm_client = await self.runtime.model_service.chat_model(memory_config.llm_model_id, tenant_id)
                logger.info("[PILOT_RUN_STREAM] LLM client initialized")
            except Exception as e:
                raise RuntimeError(f"LLM client initialization failed: {e}")
            logger.info("[PILOT_RUN_STREAM] db session closed, starting LLM pipeline")

            # ── 阶段2：无 session，LLM 管线执行 ──────────────────────────────
            # 使用队列在回调和生成器之间传递进度事件
            progress_queue: asyncio.Queue = asyncio.Queue()

            async def progress_callback(stage: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
                """
                进度回调函数，将进度事件放入队列

                Args:
                    stage: 阶段标识
                    message: 进度消息
                    data: 可选的结果数据（用于传递节点执行结果）
                """
                await progress_queue.put((stage, message, data))

            # 步骤 3: 在后台任务中执行管线（无 db 依赖）
            async def run_pipeline():
                """在后台执行管线并捕获异常"""
                try:
                    logger.info(
                        f"[PILOT_RUN_STREAM] Calling run_pilot_extraction with messages count: {len(messages)}"
                    )
                    settings = get_settings()
                    settings.ensure_memory_output_dir()
                    with TemporaryDirectory(dir=settings.get_memory_output_path()) as output_dir:
                        output_token = memory_output_override.set(Path(output_dir))
                        try:
                            await self._run_pilot_extraction(
                                memory_config=memory_config,
                                messages=messages,
                                llm_client=llm_client,
                                progress_callback=progress_callback,
                                language=language,
                                model_views=views,
                                actor_id=actor_id,
                                actor_kind=actor_kind,
                            )
                            result_path = Path(output_dir) / "extracted_result.json"
                            result_bytes = result_path.read_bytes()
                            extracted_result = json.loads(result_bytes)
                        finally:
                            memory_output_override.reset(output_token)
                    logger.info("[PILOT_RUN_STREAM] pipeline_main completed")

                    # 标记管线完成
                    await progress_queue.put(("__PIPELINE_COMPLETE__", "", None))
                    return extracted_result, result_bytes
                except Exception as e:
                    # 将异常放入队列
                    await progress_queue.put(("__PIPELINE_ERROR__", str(e), None))

            # 启动后台任务
            pipeline_task = asyncio.create_task(run_pipeline())

            # 步骤 4: 从队列中读取进度事件并发出
            while True:
                try:
                    # 等待进度事件，设置超时以检测客户端断开
                    stage, message, data = await asyncio.wait_for(
                        progress_queue.get(),
                        timeout=0.5
                    )

                    # 检查特殊标记
                    if stage == "__PIPELINE_COMPLETE__":
                        break
                    elif stage == "__PIPELINE_ERROR__":
                        raise RuntimeError(message)

                    # 构建进度事件数据
                    progress_data = {
                        "message": message,
                        "time": int(time.time() * 1000)
                    }

                    # 如果有结果数据，添加到事件中
                    if data:
                        progress_data["data"] = data

                    # 发出进度事件，使用 stage 作为事件类型
                    yield format_sse_message(stage, progress_data)

                except TimeoutError:
                    # 超时，继续等待（这允许检测客户端断开）
                    continue

            extracted_result, result_bytes = await pipeline_task
            result_data = {
                "config_id": cid,
                "time_log": None,
                "extracted_result": extracted_result,
            }
            with BytesIO(result_bytes) as content:
                try:
                    stored = await self.runtime.file_service.upload(
                        file=UploadFile(
                            content,
                            filename=f"pilot-run-{uuid.uuid4()}.json",
                            headers=Headers({"content-type": "application/json"}),
                        ),
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                        actor_kind=actor_kind,
                    )
                    result_data["result_file_id"] = stored["file_id"]
                except FileServiceError:
                    logger.warning("Pilot run result upload failed", exc_info=True)
                    result_data["result_storage_error"] = {"code": "PILOT_RESULT_UPLOAD_FAILED"}

            yield format_sse_message("result", result_data)

            # 步骤 7: 发出完成事件
            yield format_sse_message("done", {
                "message": "试运行完成",
                "time": int(time.time() * 1000)
            })

        except asyncio.CancelledError:
            # 客户端断开连接
            logger.info("[PILOT_RUN_STREAM] Client disconnected during streaming")
            raise
        except Exception as e:
            # 发出错误事件
            logger.error(f"[PILOT_RUN_STREAM] Error during streaming: {e}", exc_info=True)
            yield format_sse_message("error", {
                "code": 5000,
                "message": "试运行失败",
                "error": str(e),
                "time": int(time.time() * 1000)
            })

    @staticmethod
    def _trial_run_media_model_id(memory_config, file_type: FileType) -> uuid.UUID:
        """返回某一媒体类型的专用模型，未配置时回退文本模型。"""
        if file_type == FileType.IMAGE:
            return memory_config.vision_model_id or memory_config.llm_model_id
        if file_type == FileType.AUDIO:
            return memory_config.audio_model_id or memory_config.llm_model_id
        if file_type == FileType.VIDEO:
            return memory_config.video_model_id or memory_config.llm_model_id
        return memory_config.llm_model_id

    def _trial_run_history_round_limit(self) -> int:
        """Read the normalized limit from this service runtime configuration."""
        return self.runtime.settings.trial_run_chat_history_rounds

    @staticmethod
    def _latest_trial_run_history(history: list[Any], max_rounds: int) -> list[Any]:
        """以 user 消息为一轮的起点，保留最新 max_rounds 轮及其后续回复。"""
        if max_rounds <= 0:
            return []

        user_rounds = 0
        for index in range(len(history) - 1, -1, -1):
            if history[index].role != "user":
                continue
            user_rounds += 1
            if user_rounds == max_rounds:
                return list(history[index:])
        return list(history)

    @staticmethod
    def _normalize_modalities(modalities) -> set[str]:
        return {
            str(getattr(modality, "value", modality)).lower()
            for modality in modalities or []
        }

    @staticmethod
    def _unsupported_file_part(file_type: FileType, language: str) -> dict[str, str]:
        labels = {
            "zh": {
                FileType.IMAGE: "图片",
                FileType.AUDIO: "音频",
                FileType.VIDEO: "视频",
            },
            "en": {
                FileType.IMAGE: "image",
                FileType.AUDIO: "audio",
                FileType.VIDEO: "video",
            },
        }
        locale = "en" if language == "en" else "zh"
        label = labels[locale].get(file_type, str(file_type))
        if locale == "en":
            text = f"[The selected model does not support this {label} attachment.]"
        else:
            text = f"[当前选中的模型不支持该{label}附件。]"
        return {"type": "text", "text": text}

    @staticmethod
    def _local_file_error_part(language: str) -> dict[str, str]:
        if language == "en":
            text = "[The local attachment could not be loaded.]"
        else:
            text = "[本地附件加载失败。]"
        return {"type": "text", "text": text}

    @staticmethod
    def _file_marker_part(file, position: int, language: str) -> dict[str, str]:
        """为每个附件添加稳定的顺序和类型标记，避免模型静默忽略媒体块。"""
        file_type = str(getattr(file.type, "value", file.type))
        file_name = file.name or str(file.upload_file_id or file.url or "")
        if language == "en":
            text = f"[Attachment {position}: type={file_type}, name={file_name}]"
        else:
            text = f"[附件 {position}：类型={file_type}，名称={file_name}]"
        return {"type": "text", "text": text}

    async def _process_trial_run_files(
            self,
            files,
            multimodal_service,
            workspace_id: uuid.UUID,
            input_modalities,
            language: str,
    ) -> list[dict[str, Any]]:
        """按请求顺序处理附件，对模型不支持的类型生成可见文本提示。"""
        normalized_modalities = self._normalize_modalities(input_modalities)
        required_modalities = {
            FileType.IMAGE: "image",
            FileType.AUDIO: "audio",
            FileType.VIDEO: "video",
        }
        processed_parts: list[dict[str, Any]] = []
        for position, file in enumerate(files or [], start=1):
            required = required_modalities.get(file.type)
            if required and required not in normalized_modalities:
                processed_parts.append(self._file_marker_part(file, position, language))
                processed_parts.append(self._unsupported_file_part(file.type, language))
                continue

            if file.transfer_method == TransferMethod.LOCAL_FILE:
                try:
                    file = await multimodal_service.resolve_uploaded_file_url(file, workspace_id)
                except Exception:
                    logger.error(
                        "[TRIAL_RUN_CHAT_STREAM] Failed to resolve attachment URL: file_id=%s",
                        file.upload_file_id,
                        exc_info=True,
                    )
                    processed_parts.append(self._file_marker_part(file, position, language))
                    processed_parts.append(self._local_file_error_part(language))
                    continue

                if file.type == FileType.AUDIO:
                    processed_parts.append(self._file_marker_part(file, position, language))
                    processed_parts.append(multimodal_service.format_audio_url(file))
                    continue
                if multimodal_service.provider in {"bedrock", "anthropic"}:
                    processed_parts.append(self._file_marker_part(file, position, language))
                    processed_parts.append(self._unsupported_file_part(file.type, language))
                    continue

            parts = await multimodal_service.process_files([file], workspace_id=workspace_id)
            processed_parts.append(self._file_marker_part(file, position, language))
            processed_parts.extend(parts)
        return processed_parts

    @staticmethod
    def _stream_content_to_texts(content: Any) -> list[str]:
        """从不同 provider 的流式 content 形态中提取文本块。"""
        if isinstance(content, str):
            return [content] if content else []
        if isinstance(content, dict):
            text = content.get("text")
            return [str(text)] if text else []
        if not isinstance(content, list):
            return []

        texts: list[str] = []
        for item in content:
            if isinstance(item, str) and item:
                texts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    texts.append(str(text))
        return texts

    @staticmethod
    def _extract_message_from_event_output(output: Any) -> Any | None:
        if output is None:
            return None
        if getattr(output, "usage_metadata", None) or getattr(output, "response_metadata", None):
            return output
        if isinstance(output, dict):
            value = output.get("message") or output.get("output")
            if value is not None and not isinstance(value, (dict, list, str)):
                return value
            generations = output.get("generations")
            if isinstance(generations, list):
                for generation_group in generations:
                    items = generation_group if isinstance(generation_group, list) else [generation_group]
                    for generation in items:
                        message = getattr(generation, "message", None)
                        if message is not None:
                            return message
        return getattr(output, "message", None)

    @staticmethod
    def _extract_total_tokens(message: Any) -> int:
        if message is None:
            return 0
        response_metadata = getattr(message, "response_metadata", None)
        if isinstance(response_metadata, dict):
            usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
            if isinstance(usage, dict) and usage.get("total_tokens"):
                return int(usage["total_tokens"])
        usage_metadata = getattr(message, "usage_metadata", None)
        if isinstance(usage_metadata, dict):
            return int(usage_metadata.get("total_tokens") or 0)
        return int(getattr(usage_metadata, "total_tokens", 0) or 0)

    @staticmethod
    def _classified_llm_error_data(
            classified: ClassifiedLLMError,
            language: str,
            message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "code": int(classified.biz_code),
            "error": classified.error,
            "message": message or t(classified.i18n_key, locale=language),
        }

    @staticmethod
    def _trial_run_chat_error_data(language: str) -> dict[str, Any]:
        return {
            "code": int(BizCode.API_KEY_MISSING),
            "error": "api_key_missing",
            "message": t("errors.llm.api_key_missing", locale=language),
        }

    async def _load_trial_run_local_file(
            self,
            file,
            workspace_id: uuid.UUID,
            tenant_id: uuid.UUID,
            *,
            actor_id: UUID,
            actor_kind: str,
    ) -> tuple[bytes, str]:
        """Check attachment ownership before fetching bytes through file-service."""
        import magic

        metadata = await self.repository.metadata(
            file.upload_file_id, workspace_id=workspace_id, tenant_id=tenant_id
        )
        if metadata is None:
            raise ValueError("local attachment is unavailable in the current workspace")
        metadata_content_type = metadata.content_type
        metadata_file_ext = metadata.file_ext
        metadata_file_name = metadata.file_name

        content = await self.runtime.file_service.download(
            file.upload_file_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_kind=actor_kind,
        )
        if not content:
            raise ValueError("local attachment is empty")

        detected_mime = magic.from_buffer(content, mime=True)
        mime_type = detected_mime or metadata_content_type or "application/octet-stream"
        file.set_content(content)
        file.url = file.url or f"local-file://{file.upload_file_id}"
        if file.type == FileType.AUDIO and metadata_file_ext:
            audio_ext = metadata_file_ext.lstrip(".").lower()
            file.file_type = f"audio/{audio_ext}"
        else:
            file.file_type = mime_type
        if not file.name:
            file.name = metadata_file_name
        return content, mime_type

    async def trial_run_chat_stream(
            self,
            payload: TrialRunChatInput,
            language: str = "zh",
            workspace_id: uuid.UUID | None = None,
            tenant_id: uuid.UUID | None = None,
            *,
            actor_id: UUID,
            actor_kind: str,
    ) -> AsyncGenerator[str, None]:
        """按附件类型分别解析，再由文本模型合并生成流式回答。"""
        from src.services.model_config import ModelConfigService, ModelConfigurationError
        from langchain.agents import create_agent
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        start_time = time.perf_counter()

        attachment_groups: dict[FileType, list[dict[str, Any]]] = {
            FileType.IMAGE: [],
            FileType.AUDIO: [],
            FileType.VIDEO: [],
            FileType.DOCUMENT: [],
        }
        attachment_type_counts = {file_type: 0 for file_type in attachment_groups}
        for file_index, file in enumerate(payload.files):
            attachment_type_counts[file.type] += 1
            attachment_groups[file.type].append({
                "file_index": file_index,
                "type_index": attachment_type_counts[file.type],
                "file": file,
            })

        def attachment_label(ref: dict[str, Any]) -> str:
            file = ref["file"]
            file_type = str(getattr(file.type, "value", file.type))
            name = file.name or str(file.upload_file_id or file.url or "")
            type_labels_zh = {
                FileType.IMAGE: "图片文件",
                FileType.AUDIO: "音频文件",
                FileType.VIDEO: "视频文件",
                FileType.DOCUMENT: "文档文件",
            }
            location_zh = f"{type_labels_zh[file.type]} {ref['type_index']}"
            location_en = f"{file_type} file {ref['type_index']}"
            if language == "en":
                return f"[Details of {location_en}: name={name}]"
            return f"[{location_zh}的详细内容：名称={name}]"

        def group_failure_text(refs: list[dict[str, Any]], reason: str) -> str:
            return "\n".join(f"{attachment_label(ref)}\n{reason}" for ref in refs)

        async def build_llm(model_info: ModelInfo, *, streaming: bool, max_tokens: int) -> Any:
            """Build the API's parameterized model through the service-owned invoke client."""
            model = await self.runtime.model_service.chat_model(
                UUID(str(model_info.model_config_id)), memory_config.tenant_id
            )
            return model.model_copy(update={"default_params": {
                **model.default_params,
                "temperature": 0.2 if not streaming else 0.7,
                "max_tokens": max_tokens,
            }})

        try:
            memory_config, _ = await self._trial_config(
                payload.config_id, workspace_id, tenant_id, include_runtime_views=False
            )
            runtime_cache: dict[uuid.UUID, ModelInfo | None] = {}

            async def get_runtime(model_config_id: uuid.UUID):
                """非解密视图缓存；模型缺失/弃用/未启用按旧口径软降级为 None。"""
                if model_config_id not in runtime_cache:
                    try:
                        runtime_cache[model_config_id] = (
                            await ModelConfigService.get_runtime_model_view_async(
                                self.runtime.postgres,
                                model_config_id,
                                tenant_id=memory_config.tenant_id,
                            )
                        )
                    except ModelConfigurationError:
                        runtime_cache[model_config_id] = None
                return runtime_cache[model_config_id]

            final_runtime = await get_runtime(memory_config.llm_model_id)
            if final_runtime is None:
                yield format_sse_message("error", self._trial_run_chat_error_data(language))
                return

            media_runtimes: dict[FileType, ModelInfo | None] = {}
            required_modalities = {
                FileType.IMAGE: "image",
                FileType.AUDIO: "audio",
                FileType.VIDEO: "video",
            }
            for file_type in (FileType.IMAGE, FileType.AUDIO, FileType.VIDEO):
                if not attachment_groups[file_type]:
                    continue
                model_config_id = self._trial_run_media_model_id(memory_config, file_type)
                runtime = await get_runtime(model_config_id)
                required = required_modalities[file_type]
                if runtime is not None and required not in self._normalize_modalities(runtime.input_modalities):
                    runtime = None
                if runtime is None and model_config_id != memory_config.llm_model_id:
                    fallback = final_runtime
                    if required in self._normalize_modalities(fallback.input_modalities):
                        runtime = fallback
                media_runtimes[file_type] = runtime

            config_workspace_id = memory_config.workspace_id
            config_tenant_id = memory_config.tenant_id
            download_attachment = partial(
                self.runtime.file_service.download,
                workspace_id=config_workspace_id,
                tenant_id=config_tenant_id,
                actor_id=actor_id,
                actor_kind=actor_kind,
            )

            final_model_info = final_runtime

            yield format_sse_message("start", {
                "conversation_id": "",
                "message_id": str(uuid.uuid4()),
                "user_message_id": str(uuid.uuid4()),
            })

            async def analyze_media_group(file_type: FileType) -> dict[str, Any]:
                refs = attachment_groups[file_type]
                runtime = media_runtimes.get(file_type)
                if runtime is None:
                    classified = ClassifiedLLMError(
                        "model_not_found",
                        BizCode.MODEL_NOT_FOUND,
                        "errors.llm.model_not_found",
                    )
                    return {
                        "error": True,
                        "subject": file_type.value.lower(),
                        "classified": classified,
                    }

                model_info = runtime
                # 附件在进入试运行前已经完成输入校验，加载与格式化异常不在这里转换为 LLM 错误。
                multimodal_service = MultimodalService(
                    self.repository, model_info,
                    attachment_storage=self.runtime.attachment_storage,
                    download_attachment=download_attachment,
                )
                parts = await self._process_trial_run_files(
                    [ref["file"] for ref in refs],
                    multimodal_service,
                    config_workspace_id,
                    model_info.input_modalities,
                    language,
                )

                mapping = "\n".join(
                    f"Attachment {index} = {attachment_label(ref)}"
                    for index, ref in enumerate(refs, start=1)
                )
                requested_language = "English" if language == "en" else "Chinese"
                parser_prompt = (
                    f"{TRIAL_RUN_ATTACHMENT_ANALYSIS_PROMPT}\n"
                    f"Reply in {requested_language}.\n{mapping}"
                )
                parser_llm = await build_llm(model_info, streaming=False, max_tokens=2000)
                try:
                    response = await parser_llm.ainvoke([
                        SystemMessage(content=parser_prompt),
                        HumanMessage(content=parts),
                    ])
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "[TRIAL_RUN_CHAT_STREAM] %s attachment analysis failed",
                        file_type.value,
                        exc_info=True,
                    )
                    return {
                        "error": True,
                        "subject": file_type.value.lower(),
                        "classified": classify_llm_error(
                            e,
                            provider=model_info.provider,
                        ),
                    }
                response_text = "".join(
                    self._stream_content_to_texts(getattr(response, "content", None))
                ).strip()
                if not response_text:
                    classified = ClassifiedLLMError(
                        "empty_response",
                        BizCode.LLM_ERROR,
                        "errors.llm.empty_response",
                    )
                    return {
                        "error": True,
                        "subject": file_type.value.lower(),
                        "classified": classified,
                    }
                return {
                    "text": response_text,
                    "tokens": self._extract_total_tokens(response),
                }

            async def extract_document_group() -> dict[str, Any]:
                refs = attachment_groups[FileType.DOCUMENT]
                documents: list[str] = []
                # 文档字节在短会话关闭后下载和解析，避免长时间占用连接。
                multimodal_service = MultimodalService(
                    self.repository, final_model_info,
                    attachment_storage=self.runtime.attachment_storage,
                    download_attachment=download_attachment,
                )
                for ref in refs:
                    file = ref["file"]
                    if file.transfer_method == TransferMethod.LOCAL_FILE:
                        try:
                            file = await multimodal_service.resolve_uploaded_file_url(
                                file, config_workspace_id
                            )
                        except Exception:
                            logger.error(
                                "[TRIAL_RUN_CHAT_STREAM] Failed to resolve document URL: file_id=%s",
                                file.upload_file_id,
                                exc_info=True,
                            )
                            error = self._local_file_error_part(language)
                            documents.append(f"{attachment_label(ref)}\n{error['text']}")
                            continue
                    text = await multimodal_service.extract_document_text(file)
                    documents.append(f"{attachment_label(ref)}\n{text}")
                return {"text": "\n\n".join(documents), "tokens": 0}

            analysis_tasks = [
                analyze_media_group(file_type)
                for file_type in (FileType.IMAGE, FileType.AUDIO, FileType.VIDEO)
                if attachment_groups[file_type]
            ]
            if attachment_groups[FileType.DOCUMENT]:
                analysis_tasks.append(extract_document_group())

            analysis_results = await asyncio.gather(*analysis_tasks) if analysis_tasks else []

            attachment_errors: list[tuple[str, ClassifiedLLMError]] = []
            for result in analysis_results:
                if isinstance(result, dict) and result.get("error"):
                    attachment_errors.append((result["subject"], result["classified"]))

            if attachment_errors:
                parts = [
                    f"{t(f'errors.llm.subjects.{subject}', locale=language)} "
                    f"{t(classified.i18n_key, locale=language)}"
                    for subject, classified in attachment_errors
                ]
                combined_message = parts[0]
                if len(parts) > 1:
                    combined_message = (
                        t("errors.llm.attachment_prefix", locale=language)
                        + "；".join(parts)
                    )
                first_classified = attachment_errors[0][1]
                yield format_sse_message(
                    "error",
                    self._classified_llm_error_data(
                        first_classified,
                        language,
                        message=combined_message,
                    ),
                )
                return

            # 继续原有逻辑
            attachment_context = "\n\n".join(
                result["text"] for result in analysis_results if result.get("text")
            )
            total_tokens = sum(int(result.get("tokens") or 0) for result in analysis_results)

            history_round_limit = self._trial_run_history_round_limit()
            limited_history = self._latest_trial_run_history(
                payload.history,
                history_round_limit,
            )
            if len(limited_history) < len(payload.history):
                logger.info(
                    "[TRIAL_RUN_CHAT_STREAM] History truncated: original_messages=%s, retained_messages=%s, max_rounds=%s",
                    len(payload.history),
                    len(limited_history),
                    history_round_limit,
                )

            messages: list[Any] = []
            for history_message in limited_history:
                if history_message.role == "user":
                    messages.append(HumanMessage(content=history_message.content))
                else:
                    messages.append(AIMessage(content=history_message.content))

            current_content = payload.message
            if attachment_context:
                current_content += (
                    "\n\n<attachment_analysis_results>\n"
                    f"{attachment_context}\n"
                    "</attachment_analysis_results>"
                )
            messages.append(HumanMessage(content=current_content))

            final_llm = await build_llm(final_model_info, streaming=True, max_tokens=2000)
            requested_language = "English" if language == "en" else "Chinese"
            system_prompt = f"{TRIAL_RUN_CHAT_SYSTEM_PROMPT}\nThe requested interface language is {requested_language}."
            agent = create_agent(model=final_llm, tools=[], system_prompt=system_prompt)

            full_content = ""
            final_tokens = 0
            try:
                async for event in agent.astream_events({"messages": messages}, version="v2"):
                    event_type = event.get("event")
                    if event_type == "on_chat_model_stream":
                        chunk = event.get("data", {}).get("chunk")
                        for text_chunk in self._stream_content_to_texts(getattr(chunk, "content", None)):
                            full_content += text_chunk
                            yield format_sse_message("message", {"content": text_chunk})
                    elif event_type == "on_chat_model_end":
                        output = event.get("data", {}).get("output")
                        message = self._extract_message_from_event_output(output)
                        final_tokens = max(final_tokens, self._extract_total_tokens(message))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[TRIAL_RUN_CHAT_STREAM] Final LLM generation failed", exc_info=True)
                classified = classify_llm_error(
                    e,
                    provider=final_model_info.provider,
                )
                yield format_sse_message(
                    "error",
                    self._classified_llm_error_data(classified, language),
                )
                return

            if attachment_context:
                details_title = (
                    "Current attachment details"
                    if language == "en"
                    else "当前附件详细信息"
                )
                attachment_details = (
                    f"\n\n---\n\n## {details_title}\n\n"
                    f"{attachment_context}"
                )
                for offset in range(0, len(attachment_details), 2000):
                    details_chunk = attachment_details[offset:offset + 2000]
                    full_content += details_chunk
                    yield format_sse_message("message", {"content": details_chunk})

            total_tokens += final_tokens

            yield format_sse_message("end", {
                "elapsed_time": time.perf_counter() - start_time,
                "message_length": len(full_content),
                "usage": {"total_tokens": total_tokens},
            })
        except asyncio.CancelledError:
            logger.info("[TRIAL_RUN_CHAT_STREAM] Client disconnected during streaming")
            raise
        except ConfigurationError:
            logger.error("[TRIAL_RUN_CHAT_STREAM] Configuration loading failed", exc_info=True)
            yield format_sse_message("error", {
                "code": int(BizCode.MEMORY_CONFIG_NOT_FOUND),
                "error": "config_error",
                "message": "Configuration loading failed" if language == "en" else "配置读取失败",
            })

    async def _pilot_snapshots(
        self, messages, config, views, *, actor_id: UUID, actor_kind: str
    ):
        from types import SimpleNamespace
        from urllib.parse import unquote, urlparse

        import json_repair
        import langid

        from src.common.prompt.prompt_utils import prompt_env

        download_attachment = partial(
            self.runtime.file_service.download,
            workspace_id=config.workspace_id,
            tenant_id=config.tenant_id,
            actor_id=actor_id,
            actor_kind=actor_kind,
        )

        async def one(index, file_index, message, file):
            failure = {
                "message_index": index,
                "file_index": file_index,
                "file_type": file.type.value,
            }
            try:
                model_id = {
                    FileType.IMAGE: config.vision_model_id,
                    FileType.AUDIO: config.audio_model_id,
                    FileType.VIDEO: config.video_model_id,
                    FileType.DOCUMENT: config.llm_model_id,
                }.get(file.type)
                view = views.get(model_id)
                if view is None:
                    return None, {**failure, "code": "ATTACHMENT_MODEL_UNAVAILABLE"}
                file = file.model_copy(deep=True)
                media = MultimodalService(
                    self.repository, view,
                    attachment_storage=self.runtime.attachment_storage,
                    download_attachment=download_attachment,
                )
                if file.transfer_method == TransferMethod.LOCAL_FILE:
                    await self._load_trial_run_local_file(
                        file, config.workspace_id, config.tenant_id,
                        actor_id=actor_id, actor_kind=actor_kind,
                    )
                    file.url = await media.get_file_url(file)
                parts = await media.process_files(
                    [file], workspace_id=config.workspace_id, include_processing_errors=False
                )
                if not parts:
                    return None, {**failure, "code": "ATTACHMENT_PROCESSING_FAILED"}
                prompt = prompt_env.get_template("perceptual_summary_system.jinja2").render(
                    file_type=file.type,
                    language=langid.classify(message.content)[0] if message.content else "zh",
                )
                model = await self.runtime.model_service.chat_model(model_id, config.tenant_id)
                response = await model.ainvoke(
                    [
                        {"role": "system", "content": [{"type": "text", "text": prompt}]},
                        {
                            "role": "user",
                            "content": [
                                parts[0],
                                {
                                    "type": "text",
                                    "text": "Generate a summary of the file's content",
                                },
                            ],
                        },
                    ]
                )
                output = "".join(self._stream_content_to_texts(response.content))
                content = json_repair.repair_json(output, return_objects=True)
                filename = file.name or unquote(Path(urlparse(file.url or "").path).name)
                filename = filename or f"attachment_{file.upload_file_id or uuid.uuid4()}"
                ext = Path(filename).suffix
                if not ext:
                    ext = {
                        FileType.IMAGE: ".jpg",
                        FileType.AUDIO: ".mp3",
                        FileType.VIDEO: ".mp4",
                        FileType.DOCUMENT: ".txt",
                    }[file.type]
                    filename += ext
                modalities = (
                    {"scene": content.get("scene", [])}
                    if file.type in {FileType.IMAGE, FileType.VIDEO}
                    else (
                        {
                            "section_count": content.get("section_count", 0),
                            "title": content.get("title", ""),
                            "first_line": content.get("first_line", ""),
                        }
                        if file.type == FileType.DOCUMENT
                        else {"speaker_count": content.get("speaker_count", 0)}
                    )
                )
                snapshot = SimpleNamespace(
                    id=None,
                    end_user_id=str(config.workspace_id),
                    perceptual_type={
                        FileType.IMAGE: 1,
                        FileType.VIDEO: 1,
                        FileType.AUDIO: 2,
                        FileType.DOCUMENT: 3,
                    }[file.type],
                    file_path=file.url or "",
                    file_name=filename,
                    file_ext=ext,
                    summary=content.get("summary", ""),
                    created_time=None,
                    meta_data={
                        "content": {
                            "keywords": content.get("keywords", []),
                            "topic": content.get("topic"),
                            "domain": content.get("domain"),
                        },
                        "modalities": modalities,
                    },
                )
                return (index, snapshot, file.type.value, file.url), None
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Pilot attachment processing failed: message_index=%s", index, exc_info=True
                )
                return None, {**failure, "code": "ATTACHMENT_PROCESSING_FAILED"}

        tasks = [
            one(index, file_index, message, file)
            for index, message in enumerate(messages)
            if message.role == "user"
            for file_index, file in enumerate(message.files or [])
        ]
        results = await asyncio.gather(*tasks)
        return (
            [snapshot for snapshot, _ in results if snapshot is not None],
            [failure for _, failure in results if failure is not None],
        )



    async def delete_all_nodes_by_end_user_id(self, end_user_id: str) -> int:
        """删除指定用户的全部记忆。

        - RAG 空间：删除独立索引中该宿主的消息，返回条数。
        - 图空间：删除 Neo4j 记忆节点和边，返回节点数。
        - 归属无法判定（宿主不存在 / 未关联 workspace）：两种存储都清理，
          返回「删除条目总数」（RAG 消息条数 + 图节点数）。

        注意返回值口径：RAG 分支统计的是「消息条数」，图侧统计的是「节点数」；
        正常分支只会命中其中一种，只有归属未知的兜底分支才会相加，
        结果仅用于粗略反馈，调用方不应把它当作单一存储的精确计数做对账。
        """


        try:
            end_user_uuid = uuid.UUID(str(end_user_id))
        except (ValueError, TypeError, AttributeError):
            # 非法 id 不应继续走删除分支：用原始字符串去 ES term 查询没有意义，
            # 同时再跑一遍图删除只会放大误操作面。直接返回 0 并留 error 日志
            logger.error(
                "[MemoryService] invalid end_user_id, abort delete: %s",
                end_user_id,
            )
            return 0

        # ES term 查询与图节点都按规范形式写入（各写入入口均已解析为
        # str(end_user.id)）；这里统一用解析后的规范 id，避免调用方传入
        # 大写/无连字符 UUID 时精确匹配查不到文档、静默漏删
        canonical_end_user_id = str(end_user_uuid)

        row = await self.repository.deletion_workspace(end_user_uuid)

        if row is not None and row[1] is None:
            storage_type = "neo4j"
        else:
            storage_type = row[1] if row is not None else None

        normalized_type = str(storage_type or "").strip().lower()
        if normalized_type not in ("rag", "neo4j"):
            # 查不到归属（宿主不存在 / 未关联 workspace）或取值无法识别时，
            # 不能默认当成图空间：RAG 记忆只在 ES 里，落到图分支会静默漏删。
            # 归属未知时两种存储都尝试清理，任一失败也不阻断另一条路径。
            logger.warning(
                "[MemoryService] storage_type unresolved, delete both rag and graph: "
                "end_user=%s, storage_type=%r",
                end_user_id,
                storage_type,
            )

            # 两种存储都尝试清理，但失败必须上抛：调用层拿到返回值后会立刻
            # finalize_memory_delete，吞掉 RAG 失败会留下「宿主已删除、ES 记忆
            # 仍在」且没有重试入口。删除本身幂等，交由调用方重试收敛
            rag_error: Exception | None = None
            try:
                rag_deleted = await self.repository.delete_user_rag(canonical_end_user_id)
            except Exception as exc:
                logger.error(
                    "[MemoryService] fallback rag delete failed: end_user=%s, err=%s",
                    end_user_id,
                    exc,
                    exc_info=True,
                )
                rag_deleted = 0
                rag_error = exc

            try:
                graph_deleted = await self.repository.delete_user_graph(
                    canonical_end_user_id
                )
            except Exception as exc:
                # 图侧失败必须上抛，不能按 0 吞掉：调用层（memory_storage_controller）
                # 拿到返回值后会立刻把宿主落库为 is_active=False / memory_count=0
                # （finalize_memory_delete_async），吞异常会留下「用户显示已删除、
                # 图记忆仍在」且再无重试入口。删除本身是幂等的，交由调用方重试收敛
                logger.error(
                    "[MemoryService] fallback graph delete failed: end_user=%s, err=%s",
                    end_user_id,
                    exc,
                    exc_info=True,
                )
                raise
            logger.info(
                "[MemoryService] fallback delete done: end_user=%s "
                "rag_messages=%s graph_nodes=%s",
                end_user_id,
                rag_deleted,
                graph_deleted,
            )
            if rag_error is not None:
                # 图侧已尽量清理，但 RAG 失败仍要上抛，交由调用方重试
                raise rag_error
            # 两者单位不同（消息条数 vs 节点数），只能加总用于「删除条目总数」
            # 的粗略反馈；正常分支按 storage_type 只命中一种，不会出现混算
            return rag_deleted + graph_deleted

        if normalized_type == "rag":

            return await self.repository.delete_user_rag(canonical_end_user_id)

        return await self.repository.delete_user_graph(
            canonical_end_user_id
        )

    async def get_end_user_by_id_async(self, end_user_id):
        from types import SimpleNamespace

        row = await self.end_users.memory_user_snapshot(end_user_id)
        return SimpleNamespace(**row) if row is not None else None

    async def finalize_memory_delete_async(self, end_user_id, workspace_id):
        return await self.repository.finalize_user_delete(end_user_id, workspace_id)

    async def require_end_user_in_workspace(self, end_user_id, workspace_id):
        try:
            user_id = UUID(end_user_id)
        except ValueError as exc:
            raise ProfileError(
                BizCode.INVALID_USER_ID, "无效的终端用户ID格式", status_code=200
            ) from exc
        user = await self.end_users.resolve_user_in_workspace(user_id, workspace_id)
        if user is None or user["id"] != user_id:
            raise ProfileError(
                BizCode.PERMISSION_DENIED, "该终端用户不属于当前工作空间", status_code=200
            )

    async def delete_node_by_element_id(self, element_id, end_user_id, operator):
        """通过 elementId 删除 Neo4j 图节点（同时 DETACH DELETE 关联边）。"""
        return await self.repository.manual_delete(element_id, end_user_id, operator)

    async def sync_end_user_memory_count_from_neo4j(self, end_user_id, tenant_id):
        """Sync the counts using the runtime-owned Neo4j, PostgreSQL and Redis clients."""
        if not end_user_id:
            return 0

        node_count, memory_node_count = await self.repository.sync_count(UUID(end_user_id))
        memory_limit = (await self.runtime.quota.effective_limits(tenant_id))["end_user_memory_limit"]
        redis_client = await self.runtime.redis.client()
        if redis_client:
            if memory_node_count > memory_limit:
                await redis_client.sadd("forget:candidates", end_user_id)
                logger.info(
                    f"[MEMORY_COUNT_SYNC] 加入遗忘候选: end_user_id={end_user_id}, "
                    f"count={memory_node_count}, limit={memory_limit}"
                )
            else:
                await redis_client.srem("forget:candidates", end_user_id)

        logger.info(f"[MEMORY_COUNT_SYNC] 同步完成: end_user_id={end_user_id}, count={node_count}")
        return node_count

    async def _run_pilot_extraction(
        self,
        memory_config: MemoryConfig,
        messages: List[WriteMessageItem],
        llm_client,
        progress_callback: Optional[ProgressCallback] = None,
        language: str = "zh",
        *,
        model_views=None,
        actor_id: UUID,
        actor_kind: str,
    ) -> None:
        """执行试运行模式的知识提取流水线（v0.3.13 QA 消息格式）。

        流程：
        1. 文件 URL 解析 + 感知记忆内存快照（persist=False）
        2. 构建 DialogData → 语义剪枝 → 语义分块
        3. 调用 PilotWritePipeline 执行萃取链路
        4. 将萃取结果写入输出文件，并注入 perceptual 字段 / 移除 disambiguation

        Args:
            memory_config: 从数据库加载的内存配置对象
            messages: QA 格式消息列表（role: user/assistant，files 可选）
            llm_client: 预先初始化的 LLM 客户端（调用方负责在 db session 关闭前完成初始化）
            progress_callback: 可选的进度回调 (stage, message, data)
            language: 语言类型 ("zh" | "en")
        """
        from src.common.datetime_utils import ensure_dialog_at, utcnow_naive
        from src.common.observability.logging import get_logger
        from src.schemas.message_models import ConversationContext, ConversationMessage, DialogData
        from src.storage_services.extraction_engine.pipeline_help import (
            _write_extracted_result_summary, export_test_input_doc,
        )

        timing_logger = get_logger("memory.timing")
        timestamp = utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"=== Pilot Run Started: {timestamp} ===")

        # 统一使用 emit，避免到处判断 callback 是否为 None
        emit: ProgressCallback = progress_callback or _noop_progress
        pipeline_start = time.time()

        try:
            # ── 步骤 1: 文件 URL 解析 + 感知记忆生成（persist=False） ─────────
            step_start = time.time()
            await emit("perceptual_extract", "开始提取感知记忆", None)

            perceptual_snapshots, attachment_errors = await self._pilot_snapshots(
                messages, memory_config, model_views or {},
                actor_id=actor_id, actor_kind=actor_kind,
            )

            await emit(
                "perceptual_result", "感知记忆结果",
                {
                    "perceptual_nodes": _collect_perceptual_nodes_data(perceptual_snapshots),
                    "attachment_errors": attachment_errors,
                },
            )
            await emit("perceptual_complete", "感知记忆提取完成", {"count": len(perceptual_snapshots)})
            timing_logger.info("✓ %s: %.2fs", "Perceptual Snapshot Generation", time.time() - step_start)

            # 将 file_summary 注入回对应 message.content（对齐 WritePipeline._preprocess_files）
            message_content_map: dict[int, str] = {}
            for msg_idx, snap, _ft, _url in perceptual_snapshots:
                original = messages[msg_idx].content or ""
                tag = f"<input-file-summary>{snap.summary}</input-file-summary>"
                if snap.summary and tag not in original:
                    message_content_map[msg_idx] = original + tag
                else:
                    message_content_map.setdefault(msg_idx, original)

            # ── 步骤 2: 构建 DialogData（保留英文 role 以兼容 SemanticPruner 配对逻辑）──
            step_start = time.time()
            conv_msgs: list[ConversationMessage] = []
            for idx, m in enumerate(messages):
                content = message_content_map.get(idx, m.content or "").strip()
                if not content:
                    continue
                conv_msgs.append(
                    ConversationMessage(
                        role=m.role,
                        msg=content,
                        dialog_at=ensure_dialog_at(m.dialog_at),
                    )
                )

            if not conv_msgs:
                raise ValueError("messages 为空或所有消息内容为空")

            # 前置校验：pilot 阶段仅对 user 消息执行完整萃取，必须存在至少一条 user 消息。
            if not any(m.role == "user" for m in conv_msgs):
                raise ValueError("messages 中不存在 user 消息，pilot run 无可萃取目标")

            dialog = DialogData(
                context=ConversationContext(msgs=conv_msgs),
                ref_id="pilot_dialog_1",
                end_user_id=str(memory_config.workspace_id),
                user_id=str(memory_config.tenant_id),
                apply_id=str(memory_config.config_id),
                metadata={"source": "pilot_run", "input_type": "qa_messages"},
            )

            # ── 步骤 2.1: 语义剪枝 ────────────────────────────────────────────
            if memory_config.pruning_enabled:
                pruned_dialogs, _ = await _run_pruning(dialog, memory_config, llm_client, emit)
            else:
                pruned_dialogs = [dialog]

            # ── 步骤 2.1.5: user-only 拆分（user=抽取目标 / 全对话=上下文）────────
            # 语义对齐 write_pipeline：仅 user 参与完整萃取链路；assistant 进 supporting_context。
            # 剪枝完后可能把 user 删空（剪枝失败 fallback 回退到原 dialog，empty_fallback 也走原 dialog），
            # 在 helper 内部也会再次校验且抛 ValueError。
            for pruned in pruned_dialogs:
                _split_user_and_context(pruned)

            # ── 步骤 2.2: 语义分块 ────────────────────────────────────────────
            chunked_dialogs = await _run_chunking(pruned_dialogs, memory_config, llm_client, emit)
            timing_logger.info("✓ %s: %.2fs", "Data Loading & Chunking", time.time() - step_start)

            # ── 步骤 3: 萃取 ──────────────────────────────────────────────────
            step_start = time.time()
            logger.info("Running pilot extraction pipeline...")

            from src.pipelines.pilot_write_pipeline import PilotWritePipeline

            manager = self.runtime.model_service

            class Embedder:
                async def aembed_documents(self, texts):
                    return await manager.embed_texts(memory_config.embedding_model_id, memory_config.tenant_id, texts)

            pilot_result = await PilotWritePipeline(
                memory_config=memory_config,
                end_user_id=str(memory_config.workspace_id),
                language=language,
                progress_callback=emit,
                llm_client=llm_client,
                embedder_client=Embedder(),
            ).run(chunked_dialogs)

            timing_logger.info("✓ %s: %.2fs", "Extraction Pipeline", time.time() - step_start)

            # ── 步骤 4: 输出结果文件 ──────────────────────────────────────────
            await emit("generating_results", "正在生成结果...", None)

            graph = pilot_result.graph
            get_settings().ensure_memory_output_dir()
            export_test_input_doc(
                entity_nodes=graph.entity_nodes,
                statement_entity_edges=graph.stmt_entity_edges,
                entity_entity_edges=graph.entity_entity_edges,
            )
            _save_triplets_from_dialogs(
                dialog_data_list=pilot_result.dialog_data_list,
                output_path=get_settings().get_memory_output_path("extracted_triplets.txt"),
            )
            _write_extracted_result_summary(
                chunk_nodes=graph.chunk_nodes,
                pipeline_output_dir=get_settings().get_memory_output_path(),
            )
            _patch_extracted_result_json(_build_perceptual_summary(perceptual_snapshots))

            logger.info("Pilot run completed: stop after layer-1 dedup (no Neo4j write)")

        except Exception as e:
            logger.error(f"Pilot run failed: {e}", exc_info=True)
            raise

        total_time = time.time() - pipeline_start
        timing_logger.info("✓ %s: %.2fs", "TOTAL PILOT RUN TIME", total_time)
        logger.info(f"=== Pilot Run Completed: {utcnow_naive().strftime('%Y-%m-%d %H:%M:%S')} ===")
        logger.info(f"Pilot run complete. Total time: {total_time:.2f}s")

    def _convert_emotion_types(self, obj: Any) -> Any:
        """
        递归转换Neo4j特殊类型为可序列化的Python类型
        """
        if isinstance(obj, Neo4jDateTime):
            # 转换为用户友好的日期格式
            return self._format_emotion_datetime(obj.iso_format())
        elif hasattr(obj, "__class__") and "neo4j" in str(obj.__class__):
            if hasattr(obj, "iso_format"):
                return self._format_emotion_datetime(obj.iso_format())
            elif hasattr(obj, "__str__"):
                return str(obj)
            else:
                return repr(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_emotion_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_emotion_types(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self._convert_emotion_types(item) for item in obj)
        else:
            return obj

    def _format_emotion_datetime(self, iso_string: str) -> str:
        """
        将ISO格式的日期时间字符串转换为用户友好的格式

        Args:
            iso_string: ISO格式的日期时间字符串，如 "2026-01-07T13:40:33.679530"

        Returns:
            格式化后的日期时间字符串，如 "2026-01-07 13:40:33"
        """
        try:
            # 解析ISO格式的日期时间
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            # 返回用户友好的格式：YYYY-MM-DD HH:MM:SS
            return dt.strftime("%Y.%m")
        except (ValueError, AttributeError):
            # 如果解析失败，返回原始字符串
            return iso_string

    def _process_emotion_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理情绪查询结果，按emotion_type和created_at分组并累加emotion_intensity
        
        Args:
            results: Neo4j查询结果
            
        Returns:
            处理后的情绪数据列表，相同emotion_type和created_at的记录会合并并累加intensity
        """
        length_data=[]
        from collections import defaultdict
        
        # 用于按(emotion_type, created_at)分组累加intensity
        emotion_groups = defaultdict(float)
        
        # 检查results是否为空或不是列表
        if not results or not isinstance(results, list):
            logger.warning(f"情绪查询结果为空或格式不正确: {type(results)}")
            return []
        
        for record in results:
            # 检查record是否为字典类型
            if not isinstance(record, dict):
                logger.warning(f"跳过非字典类型的记录: {type(record)} - {record}")
                continue
                
            # 获取创建时间并格式化
            created_at = record.get('created_at')
            formatted_created_at = created_at
            
            # 如果created_at是字符串格式，尝试格式化
            if isinstance(created_at, str):
                formatted_created_at = self._format_datetime(created_at)
            emotion_type = record.get('emotion_type')
            emotion_intensity = record.get('emotion_intensity')
            if emotion_type !=None:
                length_data.append(emotion_intensity)
            if emotion_type is not None and emotion_intensity is not None and formatted_created_at is not None:
                # 使用(emotion_type, created_at)作为分组键
                # 折叠为效价（D5：10 类 + 旧值 fear/surprise）；未知值保留原值
                valence = emotion_valence(emotion_type)
                if valence is not None:
                    emotion_type = valence
                group_key = (emotion_type, formatted_created_at)
                # 累加emotion_intensity
                try:
                    emotion_groups[group_key] += float(emotion_intensity)
                except (ValueError, TypeError):
                    logger.warning(f"无法转换emotion_intensity为数字: {emotion_intensity}")
                    continue
        # 转换为最终格式
        emotion_data = [
            {
                'emotion_intensity':  round(intensity / len(length_data) * 100, 2),
                'emotion_type': emotion_type,
                'created_at': created_at
            }
            for (emotion_type, created_at), intensity in emotion_groups.items()
        ]
        
        # 按时间排序（最新的在前）
        emotion_data.sort(key=lambda x: x.get('created_at', ''), reverse=True)

        
        return emotion_data

    def _convert_interaction_types(self, obj: Any) -> Any:
        """
        递归转换Neo4j特殊类型为可序列化的Python类型
        """
        if isinstance(obj, Neo4jDateTime):
            # 转换为用户友好的日期格式
            return self._format_interaction_datetime(obj.iso_format())
        elif hasattr(obj, "__class__") and "neo4j" in str(obj.__class__):
            if hasattr(obj, "iso_format"):
                return self._format_interaction_datetime(obj.iso_format())
            elif hasattr(obj, "__str__"):
                return str(obj)
            else:
                return repr(obj)
        elif isinstance(obj, dict):
            return {k: self._convert_interaction_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_interaction_types(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self._convert_interaction_types(item) for item in obj)
        else:
            return obj

    def _format_interaction_datetime(self, iso_string: str) -> str:
        """
        将ISO格式的日期时间字符串转换为用户友好的格式

        Args:
            iso_string: ISO格式的日期时间字符串，如 "2026-01-07T13:40:33.679530"

        Returns:
            格式化后的日期时间字符串，如 "2026-01-07 13:40:33"
        """
        try:
            # 解析ISO格式的日期时间
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            # 返回用户友好的格式：YYYY-MM-DD HH:MM:SS
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            # 如果解析失败，返回原始字符串
            return iso_string

    def _process_interaction_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理交互查询结果，按季度统计交互频率
        
        Args:
            results: Neo4j查询结果
            
        Returns:
            按季度统计的交互数据列表，格式: [{"created_at": "2026Q1", "count": 3}]
        """
        from collections import defaultdict
        from datetime import datetime
        
        # 用于按季度分组计数
        quarterly_counts = defaultdict(int)
        
        for record in results:
            # 过滤掉statement为None的记录
            if not isinstance(record, dict) or record.get('statement') is None:
                continue
                
            created_at = record.get('created_at')
            if not created_at:
                continue
                
            try:
                # 处理不同类型的时间格式
                if isinstance(created_at, str):
                    # 解析ISO格式时间字符串
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif hasattr(created_at, 'year') and hasattr(created_at, 'month'):
                    # 处理Neo4j DateTime对象
                    dt = datetime(created_at.year, created_at.month, created_at.day)
                else:
                    continue
                # 计算季度
                quarter = (dt.month - 1) // 3 + 1
                quarter_key = f"{dt.year}.Q{quarter}"
                # 增加该季度的计数
                quarterly_counts[quarter_key] += 1
                
            except (ValueError, AttributeError) as e:
                logger.warning(f"解析时间失败: {e}, 原始值: {created_at}")
                continue
        
        # 转换为所需格式并按时间排序
        interaction_data = [
            {"created_at": quarter, "count": count}
            for quarter, count in quarterly_counts.items()
        ]
        
        # 按季度排序（最新的在前）
        interaction_data.sort(key=lambda x: x["created_at"], reverse=True)
        
        return interaction_data


# Trial preparation and report helpers share the trial use-case module.

ProgressCallback = Callable[[str, str, dict | None], Awaitable[None]]
PerceptualEntry = tuple[int, Any, str | None, str | None]
_ROLE_TO_CN = {"user": "用户", "assistant": "AI"}
logger = logging.getLogger(__name__)


async def _noop_progress(stage: str, message: str, data: Optional[dict] = None) -> None:
    """空回调，用于 progress_callback 为 None 时的兜底。"""


def _save_triplets_from_dialogs(dialog_data_list: list[DialogData], output_path: str) -> None:
    """Write triplet/entity text report compatible with pipeline_help parsers."""
    all_triplets: list = []
    all_entities: list = []

    for dialog in dialog_data_list:
        for chunk in getattr(dialog, "chunks", []) or []:
            for statement in getattr(chunk, "statements", []) or []:
                triplet_info = getattr(statement, "triplet_extraction_info", None)
                if not triplet_info:
                    continue
                all_triplets.extend(getattr(triplet_info, "triplets", []) or [])
                all_entities.extend(getattr(triplet_info, "entities", []) or [])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"=== EXTRACTED TRIPLETS ({len(all_triplets)} total) ===\n\n")
        for i, triplet in enumerate(all_triplets, 1):
            f.write(f"Triplet {i}:\n")
            f.write(f"  Subject: {triplet.subject_name} (ID: {triplet.subject_id})\n")
            f.write(f"  Predicate: {triplet.predicate}\n")
            f.write(f"  Object: {triplet.object_name} (ID: {triplet.object_id})\n")
            value = getattr(triplet, "value", None)
            if value:
                f.write(f"  Value: {value}\n")
            f.write("\n")

        f.write(f"\n=== EXTRACTED ENTITIES ({len(all_entities)} total) ===\n\n")
        for i, entity in enumerate(all_entities, 1):
            f.write(f"Entity {i}:\n")
            f.write(f"  ID: {entity.entity_idx}\n")
            f.write(f"  Name: {entity.name}\n")
            f.write(f"  Type: {entity.type}\n")
            f.write(f"  Description: {entity.description}\n")
            f.write("\n")


def _collect_perceptual_nodes_data(snapshots: list[PerceptualEntry]) -> list[dict]:
    """构造 perceptual_result 事件 data.perceptual_nodes。无快照时返回 []。"""
    result: list[dict] = []
    for _, snap, file_type, url in snapshots:
        content = (snap.meta_data or {}).get("content", {}) or {}
        result.append({
            "file_type": file_type,
            "file_name": snap.file_name,
            "url": url,
            "summary": snap.summary,
            "topic": content.get("topic"),
            "keywords": content.get("keywords", []),
            "domain": content.get("domain"),
        })
    return result


def _build_perceptual_summary(snapshots: list[PerceptualEntry]) -> dict:
    """构造 result.extracted_result.perceptual 字段（无快照时返回零统计 + 空数组）。"""
    file_types = [ft for _, _, ft, _ in snapshots if ft]
    type_counter = Counter(file_types)
    return {
        "count": len(snapshots),
        "file_type_distribution": [
            {"file_type": t, "count": c} for t, c in type_counter.items()
        ],
        "samples": _collect_perceptual_nodes_data(snapshots),
    }


def _patch_extracted_result_json(perceptual_summary: dict) -> None:
    """注入 perceptual 字段并移除 disambiguation 字段"""
    result_path = get_settings().get_memory_output_path("extracted_result.json")
    if not os.path.isfile(result_path):
        logger.warning(f"[PILOT_RUN] extracted_result.json 不存在，跳过 patch: {result_path}")
        return
    try:
        with open(result_path, "r", encoding="utf-8") as rf:
            data = json.load(rf)
        data["perceptual"] = perceptual_summary
        data.pop("disambiguation", None)
        with open(result_path, "w", encoding="utf-8") as wf:
            json.dump(data, wf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[PILOT_RUN] patch extracted_result.json 失败: {e}", exc_info=True)


async def _run_pruning(
    dialog: DialogData,
    memory_config: MemoryConfig,
    llm_client,
    emit: ProgressCallback,
) -> tuple[list[DialogData], dict]:
    """执行语义剪枝，返回 (pruned_dialogs, pruning_stats)。

    stats.status 取值：
    - `success`      剪枝成功，已产出 diff
    - `empty_fallback` 剪枝后为空，使用原始对话
    - `failed`       剪枝执行异常，使用原始对话
    - `disabled`     未启用（当前函数不会产生此值，由上游控制）

    enabled 始终反映配置开关（与 status 解耦），避免“启用但失败”被误读成“未启用”。
    """
    from src.schemas.config_models import PruningConfig
    from src.storage_services.extraction_engine.data_preprocessing.data_pruning import (
        SemanticPruner,
    )

    await emit("pruning_extract", "开始语义剪枝", None)

    config = PruningConfig(
        pruning_switch=memory_config.pruning_enabled,
        pruning_threshold=memory_config.pruning_threshold,
    )

    # 记录原始 user 消息用于 diff
    original_user_msgs: dict[int, str] = {
        i: m.msg for i, m in enumerate(dialog.context.msgs) if m.role == "user"
    }

    try:
        pruner = SemanticPruner(config=config, llm_client=llm_client)
        pruned_dialogs = await pruner.prune_dataset([dialog])

        if not (pruned_dialogs and pruned_dialogs[0].context):
            logger.warning("[PILOT_RUN] 剪枝后对话为空，使用原始对话")
            fallback_stats = {
                "enabled": True,
                "status": "empty_fallback",
                "threshold": config.pruning_threshold,
                "changed_count": 0,
            }
            await emit(
                "pruning_result", "语义剪枝结果",
                {"user_message_changes": [], "fallback": "剪枝后为空，使用原始对话"},
            )
            await emit("pruning_complete", "语义剪枝完成", fallback_stats)
            return [dialog], fallback_stats

        # 构建 user 消息 diff
        pruned_user_msgs = [m for m in pruned_dialogs[0].context.msgs if m.role == "user"]
        pruned_user_map: dict[int, str] = {}
        for (orig_idx, _), pmsg in zip(original_user_msgs.items(), pruned_user_msgs):
            pruned_user_map[orig_idx] = pmsg.msg

        user_message_changes = [
            {"index": idx, "original": original_user_msgs[idx], "pruned": pruned_user_map.get(idx, "")}
            for idx in original_user_msgs
            if pruned_user_map.get(idx, "") != original_user_msgs[idx]
        ]

        pruning_stats = {
            "enabled": True,
            "status": "success",
            "threshold": config.pruning_threshold,
            "changed_count": len(user_message_changes),
            "sources": dict(pruner.source_counts),
        }
        logger.info(
            f"[PILOT_RUN] 语义剪枝完成: "
            f"原始 user 消息 {len(original_user_msgs)} 条，变化 {len(user_message_changes)} 条"
        )
        await emit("pruning_result", "语义剪枝结果", {"user_message_changes": user_message_changes})
        await emit("pruning_complete", "语义剪枝完成", pruning_stats)
        return pruned_dialogs, pruning_stats

    except Exception as e:
        logger.error(f"[PILOT_RUN] 语义剪枝失败，使用原始对话: {e}", exc_info=True)
        failed_stats = {
            "enabled": True,
            "status": "failed",
            "threshold": config.pruning_threshold,
            "changed_count": 0,
            "error": str(e),
        }
        await emit("pruning_result", "语义剪枝失败", {"error": str(e), "fallback": "使用原始对话"})
        await emit("pruning_complete", "语义剪枝完成", failed_stats)
        return [dialog], failed_stats


def _split_user_and_context(dialog: DialogData) -> DialogData:
    """将对话拆分为“user 抽取目标” + “全对话上下文”。

    拆分语义对齐 write_pipeline：user 消息参与完整萃取链路（分块 -> 陈述句 ->
    三元组），assistant 消息仅作为 supporting_context 供陈述句抽取时的代词/背景解析。

    具体处理：
    - `dialog.context.msgs` 仅保留 `role == "user"` 的消息，且 role 就地改为中文 "用户"
      （对齐 chunker 内部 `Chunk(content=f"{role}: ...", speaker=role)` 的中文 prompt 约束）。
    - `dialog.metadata["supporting_context"] = {"before_msgs": [全对话中文化 MessageItem], "after_msgs": []}`，
      全部放到 before_msgs（与 orchestrator fallback “全对话已经发生”的语义一致）。
    - `dialog.content` 保持不变（上游未赋值，且 orchestrator 优先读 metadata）。

    前置条件：`dialog.context.msgs` 内容为英文 role（"user"/"assistant"），与前置
    剪枝阶段的 SemanticPruner 配对预期一致。

    Raises:
        ValueError: 拆分后 user 消息为空（无抽取目标）。
    """
    from src.storage_services.extraction_engine.steps.schema.extraction_step_schema import (
        MessageItem,
    )

    if not (dialog.context and dialog.context.msgs):
        raise ValueError("pilot run 拆分失败：dialog.context.msgs 为空")

    original_msgs = list(dialog.context.msgs)

    # 构建“全对话上下文”（保序，user+assistant 交错，中文 role）
    before_msgs = [
        MessageItem(
            role=_ROLE_TO_CN.get(m.role, m.role),
            msg=m.msg,
        )
        for m in original_msgs
        if (m.msg or "").strip()
    ]

    # 提取 user 消息，就地改中文 role
    user_msgs = [m for m in original_msgs if m.role == "user"]
    if not user_msgs:
        raise ValueError("pilot run 拆分失败：对话中不存在 user 消息，无可抽取目标")
    for m in user_msgs:
        m.role = _ROLE_TO_CN.get(m.role, m.role)  # "user" -> "用户"

    dialog.context.msgs = user_msgs
    if dialog.metadata is None:
        dialog.metadata = {}
    dialog.metadata["supporting_context"] = {
        "before_msgs": before_msgs,
        "after_msgs": [],
    }
    logger.info(
        f"[PILOT_RUN] user-only 拆分完成：user={len(user_msgs)}, "
        f"context_msgs={len(before_msgs)}"
    )
    return dialog


async def _run_chunking(
    pruned_dialogs: list[DialogData],
    memory_config: MemoryConfig,
    llm_client,
    emit: ProgressCallback,
) -> list[DialogData]:
    """对剪枝后的对话列表执行语义分块，返回 chunked_dialogs。"""
    from src.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import (
        DialogueChunker,
    )

    await emit("chunking_extract", "开始分块", {"chunker_strategy": memory_config.chunker_strategy})

    chunker = DialogueChunker(memory_config.chunker_strategy)
    chunked_dialogs: list[DialogData] = []
    for dlg in pruned_dialogs:
        dlg.chunks = await chunker.process_dialogue(dlg)
        chunked_dialogs.append(dlg)

    # 逐块汇报进度
    for dlg in chunked_dialogs:
        for i, chunk in enumerate(dlg.chunks or []):
            await emit(
                "chunking_result", f"分块 {i + 1} 处理完成",
                {
                    "chunk_index": i + 1,
                    "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                    "full_length": len(chunk.content),
                    "dialog_id": dlg.id,
                    "chunker_strategy": memory_config.chunker_strategy,
                },
            )

    await emit(
        "chunking_complete", "分块完成",
        {
            "total_chunks": sum(len(dlg.chunks or []) for dlg in chunked_dialogs),
            "total_dialogs": len(chunked_dialogs),
            "chunker_strategy": memory_config.chunker_strategy,
        },
    )
    return chunked_dialogs
