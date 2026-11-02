"""Skill Schema 定义"""
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_serializer
import uuid
from datetime import datetime

from app.core.utils.datetime_utils import to_timestamp_ms


class SkillCreate(BaseModel):
    """创建 Skill（一律创建标准格式）

    `skill_md`：SKILL.md 全文（含 YAML frontmatter 头部块）；
    `files`：可选附加文件 {rel_path: 文本内容}，如 references/guide.md。
    不再支持以 legacy 表单字段（name/prompt/keywords/tools）创建。
    """
    skill_md: str = Field(..., description="SKILL.md 全文（含 YAML frontmatter 头部块）")
    files: Optional[Dict[str, str]] = Field(
        None, description="附加文件 {rel_path: 文本内容}，如 references/guide.md、scripts/run.py",
    )


class SkillUpdate(BaseModel):
    """更新 Skill

    - standard 技能：传 skill_md / files 更新包内容（整体替换）
    - legacy 技能：传 name / description / tools / config / prompt 更新行字段（存量兼容）
    """
    name: Optional[str] = None
    description: Optional[str] = None
    tools: Optional[List[Dict[str, str]]] = None
    config: Optional[Dict[str, Any]] = None
    prompt: Optional[str] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    skill_md: Optional[str] = Field(None, description="SKILL.md 全文（含 frontmatter），standard 技能用于更新包内容")
    files: Optional[Dict[str, str]] = Field(None, description="附加文件 {rel_path: 文本内容}（整体替换）")


class Skill(BaseModel):
    """Skill 响应 Schema"""
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
    # 以下 2 个字段为「追加」，旧字段与旧取值全部不变，旧前端不受影响
    skill_format: str = Field("legacy", description="技能格式：legacy | standard")
    package_id: Optional[uuid.UUID] = Field(None, description="关联标准包 id（standard 时非空）")
    # 以下 3 个字段仅在详情接口对 standard 技能返回（编辑器回显用），列表接口为 None
    skill_md: Optional[str] = Field(None, description="SKILL.md 全文")
    files: Optional[List[Dict[str, Any]]] = Field(None, description="包内附加文件 [{rel_path, content, size, sha256}]")
    has_scripts: Optional[bool] = Field(None, description="是否含 scripts/ 目录")

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime_to_timestamp(self, value: datetime) -> int:
        """（毫秒级）时间戳"""
        return to_timestamp_ms(value)

    class Config:
        from_attributes = True


class SkillQuery(BaseModel):
    """Skill 查询参数"""
    search: Optional[str] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    page: int = Field(1, ge=1)
    pagesize: int = Field(10, ge=1, le=100)
