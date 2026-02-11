"""Data models for Yuque integration."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class YuqueRepoInfo:
    """Repository (知识库) information from Yuque."""
    id: int # 知识库 ID
    type: str # 类型 (Book:文档, Design:图集, Sheet:表格, Resource:资源)
    name: str  # 名称
    namespace: str  # 完整路径: user/repo format
    slug: str # 路径
    description: Optional[str] # 简介
    public: int  # 公开性 (0:私密, 1:公开, 2:企业内公开)
    items_count: int  # 文档数量
    created_at: datetime # 创建时间
    updated_at: datetime # 更新时间


@dataclass
class YuqueDocInfo:
    """Document information from Yuque."""
    id: int # 文档 ID
    type: str  # 文档类型 (Doc:普通文档, Sheet:表格, Thread:话题, Board:图集, Table:数据表)
    slug: str # 路径
    title: str # 标题
    book_id: int  # 归属知识库 ID
    format: str  # 内容格式 (markdown:Markdown 格式, lake:语雀 Lake 格式, html:HTML 标准格式, lakesheet:语雀表格)
    body: Optional[str]  # 正文原始内容
    body_draft: Optional[str] # 正文草稿内容
    body_html: Optional[str] # 正文 HTML 标准格式内容
    public: int  # 公开性 (0:私密, 1:公开, 2:企业内公开)
    status: int  # 状态 (0:草稿, 1:发布)
    created_at: datetime # 创建时间
    updated_at: datetime # 更新时间
    published_at: Optional[datetime] # 发布时间
    word_count: int # 内容字数
    cover: Optional[str] # 封面
    description: Optional[str] # 摘要
