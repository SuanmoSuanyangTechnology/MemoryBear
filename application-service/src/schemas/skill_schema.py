"""Skill-domain schemas (port of the monolith skill_schema, plus the standard-pack face).

Field names, defaults, descriptions, and serializers must stay byte-compatible
with the host so responses remain wire-identical during coexistence. The
standard-pack fields are append-only extensions: legacy rows keep the same wire
shape they had before the rewrite.

The legacy write schemas (``SkillCreate`` / ``SkillUpdate``) are gone with their
endpoints — the import pipeline is the only write entry point (design doc ADR-9).
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_serializer
import uuid
from datetime import datetime

from ..utils.datetime_utils import to_timestamp_ms


class SkillFileIn(BaseModel):
    """Skill file (list payload + export rebuild).

    ``rel_path`` is pack-relative only: absolute paths, ``..`` traversal and
    over-long paths are rejected upstream (design doc §9.1).
    """
    rel_path: str = Field(..., description="包内相对路径，如 SKILL.md、references/faq.md")
    content: Optional[str] = Field(None, description="文本内容；二进制文件为 None")
    size: int = Field(0, description="字节数")
    is_binary: bool = Field(False, description="是否二进制")

    class Config:
        from_attributes = True


class SkillImportMember(BaseModel):
    """Member-level metadata override carried by an import request.

    ``identifier`` is the member key exactly as returned by the ``dry_run=true``
    preview (multi-member layouts: the member's relative directory; flat layouts:
    the frontmatter name). ``call_name`` becomes the runtime identifier, so it
    must match ``^[A-Za-z0-9_-]{1,64}$`` and be unique inside the tenant.
    """
    identifier: str = Field(..., description="成员键，由 dry_run 预览原样返回")
    name: Optional[str] = Field(None, description="成员展示名，可覆盖")
    description: Optional[str] = Field(None, description="成员描述，可覆盖")
    call_name: Optional[str] = Field(None, description="调用名，^[A-Za-z0-9_-]{1,64}$，租户内唯一")


class Skill(BaseModel):
    """Skill response schema."""
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: Optional[str] = None
    tools: Union[List[Dict[str, Any]], List[Dict[str, str]]] = Field(default_factory=list, description="工具列表，可以是简单格式或包含工具详情")
    config: Dict[str, Any] = Field(default_factory=dict)
    prompt: Optional[str] = None
    is_active: bool = True
    is_public: bool = False
    created_at: datetime
    updated_at: datetime

    # Agent Skills standard-pack face. Legacy rows keep the defaults below, so
    # the old wire shape is unchanged for them (skill_format='legacy', kind='skill').
    skill_format: str = Field("legacy", description="技能格式：legacy | standard")
    package_id: Optional[uuid.UUID] = Field(None, description="关联标准包 id（standard 时非空）")
    kind: str = Field("skill", description="卡片粒度：skill（独立技能）| plugin（插件行）")
    plugin_id: Optional[uuid.UUID] = Field(None, description="插件成员的归属插件")
    call_name: Optional[str] = Field(None, description="运行时调用名（read_skill / discovery 索引标识）")
    source: Optional[str] = Field(None, description="来源展示（GitHub 地址 / zip 文件名）")
    # Detail-only (standard skills and plugin rows); the list leaves them None/empty.
    skill_md: Optional[str] = Field(None, description="SKILL.md 全文")
    files: Optional[List[SkillFileIn]] = Field(None, description="包内文件（二进制不含内容）")
    has_scripts: Optional[bool] = Field(None, description="是否含 scripts/ 目录")
    members: Optional[List[Dict[str, Any]]] = Field(None, description="kind=plugin 时的成员摘要")

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime_to_timestamp(self, value: datetime) -> int:
        """Millisecond timestamp."""
        return to_timestamp_ms(value)

    class Config:
        from_attributes = True


class SkillQuery(BaseModel):
    """Skill query parameters."""
    search: Optional[str] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    kind: str = Field("all", description="粒度过滤：all | plugin | skill")
    page: int = Field(1, ge=1)
    pagesize: int = Field(10, ge=1, le=100)
