"""The single column/API/legacy mapping for parser configuration."""

from dataclasses import dataclass
from enum import Flag, auto
from typing import Any
from uuid import UUID


class Scope(Flag):
    KNOWLEDGE = auto()
    DOCUMENT = auto()
    COMMON = KNOWLEDGE | DOCUMENT


@dataclass(frozen=True)
class FieldSpec:
    column: str
    api_path: str
    legacy_path: str | None
    py_type: type
    legacy_default: Any
    scope: Scope = Scope.COMMON
    nullable: bool = False
    allowed: tuple[Any, ...] = ()
    secret: bool = False
    aliases: tuple[str, ...] = ()
    owner: bool = False

    @property
    def description(self) -> str:
        return FIELD_DESCRIPTIONS[self.column]

    def path(self, scope: Scope) -> str:
        prefix = "defaults." if self.scope == Scope.COMMON and scope == Scope.KNOWLEDGE else ""
        return prefix + self.api_path

    def is_nullable(self, scope: Scope) -> bool:
        return self.nullable or (
            scope == Scope.KNOWLEDGE and self.column in {"chunk_method", "auto_questions"}
        )

    def nullable_meaning(self, scope: Scope) -> str | None:
        if not self.is_nullable(scope):
            return None
        if self.column in {"chunk_method", "auto_questions"}:
            return "历史未定分块模式"
        if self.py_type is UUID:
            return "继承库或空间模型" if scope == Scope.DOCUMENT else "回退空间默认模型"
        if self.column == "qa_prompt":
            return "使用系统提示词"
        if self.column == "doc_type":
            return "普通文档"
        return "当前来源或平台不适用"


K = Scope.KNOWLEDGE
D = Scope.DOCUMENT
FIELDS = (
    FieldSpec(
        "layout_recognize",
        "parsing.layout",
        "layout_recognize",
        str,
        "plain",
        allowed=("plain", "mineru", "textln"),
    ),
    FieldSpec("html4excel", "parsing.html4excel", "html4excel", bool, False),
    FieldSpec("analyze_hyperlink", "parsing.follow_hyperlinks", "analyze_hyperlink", bool, False),
    FieldSpec(
        "markdown_hyperlink_enabled",
        "parsing.extract_markdown_hyperlinks",
        "hyperlink_urls",
        bool,
        False,
    ),
    FieldSpec("image_vision_enabled", "image.enabled", "image.vision_enabled", bool, True),
    FieldSpec("image_vision_mode", "image.mode", "image.vision_mode", int, 1, allowed=(0, 1, 2)),
    FieldSpec(
        "embedded_image_vision_enabled",
        "image.embedded_enabled",
        "embedded_image.vision_enabled",
        bool,
        True,
    ),
    FieldSpec(
        "chunk_method",
        "chunking.method",
        None,
        str,
        None,
        allowed=("default", "custom", "parent_child"),
        aliases=("parent_child_mode",),
    ),
    FieldSpec("chunk_token_num", "chunking.max_tokens", "chunk_token_num", int, 128),
    FieldSpec("chunk_overlap", "chunking.overlap_tokens", "chunk_overlap", int, 0),
    FieldSpec(
        "delimiters",
        "chunking.delimiters",
        "delimiter",
        list,
        ["\n", "!", "?", "。", "；", "！", "？"],
    ),
    FieldSpec("auto_keywords", "chunking.auto_keywords", "auto_keywords", int, 0),
    FieldSpec(
        "parent_chunk_mode",
        "chunking.parent.mode",
        "parent_chunk_mode",
        str,
        "paragraph",
        allowed=("paragraph", "full-doc"),
    ),
    FieldSpec(
        "parent_chunk_token_num", "chunking.parent.max_tokens", "parent_chunk_token_num", int, 1024
    ),
    FieldSpec(
        "parent_chunk_delimiters",
        "chunking.parent.delimiters",
        "parent_chunk_delimiter",
        list,
        ["\n\n"],
    ),
    FieldSpec("auto_questions", "qa.questions_per_chunk", "auto_questions", int, 0),
    FieldSpec("qa_prompt", "qa.prompt", "qa_prompt", str, None, nullable=True),
    FieldSpec(
        "doc_type",
        "parsing.document_kind",
        "doc_type",
        str,
        None,
        D,
        nullable=True,
        allowed=("qa",),
    ),
    FieldSpec("multimodal_enabled", "multimodal.enabled", None, bool, False, K),
    FieldSpec("multimodal_enabled", "multimodal.enabled", None, bool, False, D),
    FieldSpec("vision_model_id", "multimodal.vision_model_id", None, UUID, None, D, nullable=True),
    FieldSpec("audio_model_id", "multimodal.audio_model_id", None, UUID, None, D, nullable=True),
    FieldSpec("video_model_id", "multimodal.video_model_id", None, UUID, None, D, nullable=True),
    FieldSpec(
        "image2text_id",
        "multimodal.vision_model_id",
        None,
        UUID,
        None,
        K,
        nullable=True,
        owner=True,
    ),
    FieldSpec(
        "audio2text_id", "multimodal.audio_model_id", None, UUID, None, K, nullable=True, owner=True
    ),
    FieldSpec(
        "video2text_id", "multimodal.video_model_id", None, UUID, None, K, nullable=True, owner=True
    ),
    FieldSpec("graph_enabled", "graph.enabled", "graphrag.use_graphrag", bool, False, K),
    FieldSpec("graph_scene_name", "graph.scene_name", "graphrag.scene_name", str, "", K),
    FieldSpec("graph_entity_types", "graph.entity_types", "graphrag.entity_types", list, [], K),
    FieldSpec("graph_method", "graph.method", "graphrag.method", str, "general", K),
    FieldSpec("graph_resolution", "graph.resolution", "graphrag.resolution", bool, True, K),
    FieldSpec("graph_community", "graph.community", "graphrag.community", bool, True, K),
    FieldSpec(
        "graph_pipeline",
        "graph.pipeline",
        "graphrag.pipeline",
        str,
        "legacy",
        K,
        allowed=("legacy", "evidence"),
    ),
    FieldSpec("wiki_language", "wiki.language", "wiki.language", str, "zh-CN", K),
    FieldSpec("wiki_extract_prompt", "wiki.extract_prompt", "wiki.extract_prompt", str, "", K),
    FieldSpec(
        "web_entry_url", "sources.website.entry_url", "entry_url", str, None, K, nullable=True
    ),
    FieldSpec("web_max_pages", "sources.website.max_pages", "max_pages", int, 20, K, nullable=True),
    FieldSpec(
        "web_delay_seconds",
        "sources.website.delay_seconds",
        "delay_seconds",
        float,
        1.0,
        K,
        nullable=True,
    ),
    FieldSpec(
        "web_timeout_seconds",
        "sources.website.timeout_seconds",
        "timeout_seconds",
        float,
        10.0,
        K,
        nullable=True,
    ),
    FieldSpec(
        "web_user_agent",
        "sources.website.user_agent",
        "user_agent",
        str,
        "KnowledgeBaseCrawler/1.0",
        K,
        nullable=True,
    ),
    FieldSpec(
        "third_party_platform",
        "sources.third_party.platform",
        "_third_party_platform",
        str,
        None,
        K,
        nullable=True,
        allowed=("yuque", "feishu"),
    ),
    FieldSpec(
        "yuque_user_id",
        "sources.third_party.yuque.user_id",
        "yuque_user_id",
        str,
        None,
        K,
        nullable=True,
    ),
    FieldSpec(
        "yuque_token",
        "sources.third_party.yuque.token",
        "yuque_token",
        str,
        None,
        K,
        nullable=True,
        secret=True,
    ),
    FieldSpec(
        "feishu_app_id",
        "sources.third_party.feishu.app_id",
        "feishu_app_id",
        str,
        None,
        K,
        nullable=True,
    ),
    FieldSpec(
        "feishu_app_secret",
        "sources.third_party.feishu.app_secret",
        "feishu_app_secret",
        str,
        None,
        K,
        nullable=True,
        secret=True,
    ),
    FieldSpec(
        "feishu_folder_token",
        "sources.third_party.feishu.folder_token",
        "feishu_folder_token",
        str,
        None,
        K,
        nullable=True,
        secret=True,
    ),
)


def fields_for(scope: Scope) -> tuple[FieldSpec, ...]:
    if scope not in (K, D):
        raise ValueError("a single configuration scope is required")
    return tuple(field for field in FIELDS if field.scope & scope)


def validate_registry(fields: tuple[FieldSpec, ...] = FIELDS) -> None:
    for scope in (K, D):
        columns: set[str] = set()
        paths: set[str] = set()
        legacy_paths: set[str] = set()
        for field in fields:
            if not field.scope & scope:
                continue
            for seen, values in (
                (columns, (field.column,)),
                (paths, (field.path(scope),)),
                (legacy_paths, ((field.legacy_path,) if field.legacy_path else ()) + field.aliases),
            ):
                for value in values:
                    if value in seen:
                        raise ValueError(f"duplicate registry path: {value}")
                    seen.add(value)


validate_registry()


FIELD_DESCRIPTIONS = {
    "layout_recognize": "PDF/文档版面解析方式",
    "html4excel": "表格是否转换为 HTML",
    "analyze_hyperlink": "是否解析文档内超链接来源",
    "markdown_hyperlink_enabled": "是否提取 Markdown 链接；旧 hyperlink_urls 实际是开关",
    "image_vision_enabled": "是否处理直接上传的图片",
    "image_vision_mode": "图片解析方式：0 OCR 文本图片、1 OCR 与图像混合、2 纯图像",
    "embedded_image_vision_enabled": "是否处理文档内嵌图片",
    "chunk_method": "分块方式：default 系统默认、custom 自定义普通分块、parent_child 父子分块",
    "chunk_token_num": "子块/普通块最大 token 数",
    "chunk_overlap": "相邻分块重叠 token 数",
    "delimiters": "有序分隔符；多字符值作为一个分隔符，运行时按反引号规则转换",
    "auto_keywords": "每块自动关键词数量，0 表示关闭",
    "parent_chunk_mode": "父块模式：paragraph 按段落，full-doc 全文",
    "parent_chunk_token_num": "父块最大 token 数",
    "parent_chunk_delimiters": "有序父块分隔符",
    "auto_questions": "每块自动生成问题数，0 关闭；父子分块和仅 Wiki 策略必须为 0",
    "qa_prompt": "自动问题生成提示词；null 使用系统提示词",
    "doc_type": "文档类型标记；qa 为问答文档，null 为普通文档",
    "multimodal_enabled": "多模态总开关；false 时三个模态均不调用模型",
    "vision_model_id": "文档视觉模型；null 继承库模型，再回退空间默认；需校验租户和能力",
    "audio_model_id": "文档音频模型；null 继承库模型，再回退空间默认；需校验租户和能力",
    "video_model_id": "文档视频模型；null 继承库模型，再回退空间默认；需校验租户和能力",
    "image2text_id": "库默认模型；null 表示运行时回退到空间默认；需属于当前租户且模型类型/能力匹配",
    "audio2text_id": "库默认模型；null 表示运行时回退到空间默认；需属于当前租户且模型类型/能力匹配",
    "video2text_id": "库默认模型；null 表示运行时回退到空间默认；需属于当前租户且模型类型/能力匹配",
    "graph_enabled": "是否启用图谱加工；不受 RAG 位限制",
    "graph_scene_name": "图谱场景名；允许空串",
    "graph_entity_types": "有序实体类型列表；沿用现有图谱实体校验",
    "graph_method": "图谱方法；无新增枚举限制",
    "graph_resolution": "是否进行实体消歧",
    "graph_community": "是否进行社区发现",
    "graph_pipeline": "图谱管线；新建默认 evidence；历史缺失迁移为 legacy",
    "wiki_language": "Wiki 生成语言；长度按去空白后校验",
    "wiki_extract_prompt": "Wiki 提取提示词",
    "web_entry_url": "网站入口 URL，source=website 时创建必填；无隐式网址默认值",
    "web_max_pages": "单次抓取最大页面数，缺省 20",
    "web_delay_seconds": "页面请求间隔秒数，缺省 1.0",
    "web_timeout_seconds": "请求超时秒数，必须大于 0，缺省 10",
    "web_user_agent": "爬虫 User-Agent，缺省 KnowledgeBaseCrawler/1.0",
    "third_party_platform": "source=third_party 时创建必填；创建后不可变",
    "yuque_user_id": "platform=yuque 时创建必填；语雀用户 ID，拒绝占位值",
    "yuque_token": "platform=yuque 时创建必填；PATCH 省略保留、提交替换；拒绝空值与占位值",
    "feishu_app_id": "platform=feishu 时创建必填；飞书应用 ID，拒绝占位值",
    "feishu_app_secret": "platform=feishu 时创建必填；PATCH 省略保留、提交替换；拒绝空值与占位值",
    "feishu_folder_token": "platform=feishu 时创建必填；PATCH 省略保留、提交替换；拒绝空值与占位值",
}
