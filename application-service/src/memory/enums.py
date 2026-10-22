from enum import Enum, StrEnum


class StorageType(StrEnum):
    NEO4J = 'neo4j'
    RAG = 'rag'


class Neo4jStorageStrategy(StrEnum):
    WINDOW = 'window'
    TIMELINE = 'timeline'
    AGGREGATE = "aggregate"


class SearchStrategy(StrEnum):
    DEEP = "0"
    NORMAL = "1"
    QUICK = "2"
    EXPRESS = "5"

    RECENT = "3"
    META = "4"

    @classmethod
    def _missing_(cls, value: str):
        aliases = {
            "deep": cls.DEEP,
            "normal": cls.NORMAL,
            "quick": cls.QUICK,
            "recent": cls.RECENT,
            "meta": cls.META,
            "express": cls.EXPRESS,
            "conv": cls.RECENT  # NOTE: backwards compatibility
        }
        method = aliases.get(str(value).lower())
        if not method:
            raise Exception(f"Unknown storage strategy {value}")
        return method


class Neo4jNodeType(StrEnum):
    CHUNK = "Chunk"
    COMMUNITY = "Community"
    DIALOGUE = "Dialogue"
    EXTRACTEDENTITY = "ExtractedEntity"
    MEMORYSUMMARY = "MemorySummary"
    PERCEPTUAL = "Perceptual"
    STATEMENT = "Statement"

    RAG = "Rag"
    HISTORY = "HISTORY"

class MemoryDisplayType(StrEnum):
    """Stable memory categories for retrieval-result display."""

    PROFILE = "profile"
    FACT = "fact"
    ENTITY = "entity"
    SUMMARY = "summary"
    FILE = "file"
    UNKNOWN = "unknown"

class TripletPredicate(Enum):
    """
    Single-level predicate ontology (Predicate Ontology) currently used by extract_triplet

    predicate_id:
        Numeric enum predicate ID on relationship edges in Neo4j

    value:
        Standard predicate name used for LLM output

    description:
        Semantic definition and usage constraints of the current predicate
    """

    ALIAS_OF = (
        1,
        "别名属于",
        "表达实体名称、别名、称呼之间的对应关系；"
        "仅用于名字性表达，不用于职业、角色、评价词。"
    )

    IS_A = (
        2,
        "属于类型",
        "表达主体所属的身份、职业、角色、组织或群体；"
        "用于稳定归属关系。"
    )

    LOCATED_IN = (
        3,
        "位于",
        "表达实体与地点、场所之间的稳定空间位置关系；"
        "不用于时间表达或未解析位置指代。"
    )

    VISITS = (
        4,
        "前往",
        "表达主体前往、到访某地点、组织或场所；"
        "优先保留具有长期记忆价值的到访关系。"
    )

    PART_OF = (
        5,
        "组成部分",
        "表达部分与整体之间的结构包含关系；"
        "采用 part-to-whole 方向。"
    )

    OWNS = (
        6,
        "拥有",
        "表达主体拥有、持有、配有某对象、账号、联系方式或生命体；"
        "不用于抽象概念或情绪状态。"
    )

    USES = (
        7,
        "使用",
        "表达主体使用某工具、平台、语言、资源或设备；"
        "不用于抽象结果。"
    )

    CREATED = (
        8,
        "创建了",
        "表达主体创建、生产、撰写某对象；"
        "方向为 创建者 -> 创建了 -> 被创建对象。"
    )

    KNOWS = (
        9,
        "了解",
        "表达主体对知识、技能、语言或学科具有学习、了解或认知关系；"
        "关系对象必须属于知识能力类。"
    )

    PREFERS = (
        10,
        "偏好",
        "表达主体的稳定偏好、习惯或明确目标倾向；"
        "不用于抽象愿望或情绪状态。"
    )

    RESPONSIBLE_FOR = (
        11,
        "负责",
        "表达主体负责某项事务、工作、项目或职责；"
        "对象应为具体事务。"
    )

    COMMUNICATES_WITH = (
        12,
        "沟通于",
        "表达两个主体之间存在沟通、交流或交互关系；"
        "两端通常应为可交互主体。"
    )

    RELATED_TO = (
        13,
        "关联于",
        "受限弱关联关系；"
        "仅用于明确稳定但缺少更精确谓词的关系；"
        "不能作为兜底关系。"
    )

    def __new__(cls, predicate_id: int, value: str, description: str):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.predicate_id = predicate_id
        obj.description = description
        return obj

    @property
    def predicate(self) -> str:
        return self._value_

    def to_dict(self) -> dict:
        return {
            "predicate_id": self.predicate_id,
            "predicate": self.value,
            "description": self.description,
        }

    @classmethod
    def prompt_definitions(cls) -> str:
        """
        Ontology prompt text provided to the LLM
        """
        return "\n".join(
            f"- {item.predicate_id} ({item.value}): {item.description}"
            for item in cls
        )


class MemoryMessageSource(StrEnum):
    """memory_messages.source enum — identifies the write source of a memory message.

    - AGENT: agent conversation (uses conversation_id)
    - SERVICE_API: written via /writer_service_async (conversation_id is NULL)
    - MCP: written by an MCP client (conversation_id is NULL)
    - WORKFLOW: written by MemoryWriteNode of a conversation-flow workflow (uses conversation_id)
    """

    AGENT = "agent"
    SERVICE_API = "service_api"
    MCP = "mcp"
    WORKFLOW = "workflow"
