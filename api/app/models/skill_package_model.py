"""Skill Package 模型定义

Agent Skills 开放标准（SKILL.md 格式）的数据库承载：
- SkillPackage：包清单（frontmatter 元数据 + content_hash 幂等键）
- SkillFile：包内文件（文本内联 DB，二进制/超大走对象存储）
- SkillPlugin：插件分组（一个文件夹多技能），只承载分组与来源，无运行时内容
"""
import datetime
import uuid
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSON

from app.db import Base
from app.core.utils.datetime_utils import utcnow_naive


class SkillPackage(Base):
    """技能包清单 - 标准 Agent Skills 包的元数据行

    同租户同 name 唯一（一个技能一个包，不做多版本并存）；
    content_hash 用于导入幂等与内容 diff。frontmatter 里的 metadata.version
    原样保留在 frontmatter_raw，不参与任何逻辑。
    插件成员包通过 plugin_id 归属插件；独立导入的技能该列为 NULL。
    """
    __tablename__ = "skill_packages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_skill_pkg_tenant_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")

    # 标准 frontmatter 字段
    name = Column(String(64), nullable=False, comment="标准 frontmatter name（小写字母/数字/连字符）")
    display_name = Column(String(255), comment="展示名（legacy 技能原名），标准协议不感知")
    description = Column(Text, comment="frontmatter description（≤1024 字符）")

    # 插件归属（原型「一个文件夹多技能」的分组列；NULL = 独立技能）
    plugin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("skill_plugins.id"),
        nullable=True,
        index=True,
        comment="所属插件；NULL 表示独立导入的技能",
    )

    # 包级校验与来源
    content_hash = Column(String(64), nullable=False, index=True, comment="全包内容 SHA-256（文件级 sha256 按序聚合）")
    source_type = Column(String(16), nullable=False, default="imported", comment="来源：imported | market")
    source_ref = Column(String(1024), comment="导入来源（GitHub URL / 文件名），审计用")

    # frontmatter 完整保留（license/compatibility/metadata/allowed-tools 原样）
    frontmatter_raw = Column(JSON, default=dict, comment="解析后的完整 frontmatter")
    license = Column(String(255), comment="许可证（冗余列，合规扫描直读）")
    compatibility = Column(Text, comment="环境要求（冗余列，安装提示用）")

    # 冗余标记
    has_scripts = Column(Boolean, default=False, nullable=False, comment="scripts/ 目录非空（当前仅存储，需沙箱执行）")
    has_assets = Column(Boolean, default=False, nullable=False, comment="存在 assets/ 或对象存储文件")

    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")

    def __repr__(self):
        return f"<SkillPackage(id={self.id}, name={self.name})>"


class SkillFile(Base):
    """技能包内文件 - rel_path 唯一，文本内联 / 二进制走对象存储"""
    __tablename__ = "skill_files"
    __table_args__ = (
        UniqueConstraint("package_id", "rel_path", name="uq_skill_file_pkg_path"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    package_id = Column(
        UUID(as_uuid=True),
        ForeignKey("skill_packages.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="所属包",
    )
    rel_path = Column(String(1024), nullable=False, comment="相对包根路径，如 SKILL.md、references/faq.md")
    content = Column(Text, comment="文本内容（UTF-8）；二进制/超大文件为 null")
    storage_ref = Column(String(1024), comment="对象存储引用；与 content 互斥")
    is_binary = Column(Boolean, default=False, nullable=False, comment="是否二进制")
    size = Column(Integer, default=0, nullable=False, comment="字节数")
    sha256 = Column(String(64), nullable=False, comment="单文件 SHA-256")

    def __repr__(self):
        return f"<SkillFile(package_id={self.package_id}, rel_path={self.rel_path})>"


class SkillPlugin(Base):
    """插件分组表 - 「一个文件夹下多个技能」的分组与来源

    插件本身不承载运行内容：不可被 read_skill 调用、不进 discovery 索引、
    不作为 Agent skill_ids 的绑定对象。成员是一等公民——每个成员各自一个
    SkillPackage + 一行 skills。
    """
    __tablename__ = "skill_plugins"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_skill_plugin_tenant_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")

    name = Column(String(255), nullable=False, comment="插件展示名（导入向导可编辑）")
    description = Column(Text, comment="插件描述（导入向导可编辑）")

    source_type = Column(String(16), nullable=False, default="imported", comment="来源：imported（URL / zip）")
    source_ref = Column(String(1024), comment="来源（GitHub 地址 / zip 文件名），审计")

    created_at = Column(DateTime, default=utcnow_naive, comment="创建时间")
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, comment="更新时间")

    def __repr__(self):
        return f"<SkillPlugin(id={self.id}, name={self.name})>"
