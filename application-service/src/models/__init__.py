"""Aggregate entry point for the app-domain tables: importing this package
registers all 39 tables plus the FK anchors in `ServiceBase.metadata`.
"""

from . import fk_anchors  # noqa: F401  FK anchors for the five cross-domain key targets
from .agent_app_config_model import AgentConfig
from .agent_execution_model import AgentExecution
from .annotation_model import AppAnnotation, AppAnnotationHitLog, AppAnnotationSetting, HitLogSource
from .app_model import App, AppStatus, AppType, AppVisibility, IconType
from .app_release_model import AppRelease
from .appshare_model import AppShare
from .conversation_model import Conversation, ConversationContextState, ConversationDetail, Message
from .conversation_share_model import ConversationShare
from .end_user_info_model import EndUserInfo
from .end_user_model import EndUser, EndUserMerge
from .file_metadata_model import FileMetadata
from .generic_file_model import GenericFile
from .mcp_market_config_model import McpMarketConfig
from .mcp_market_model import McpMarket
from .message_feedback_model import MessageFeedback
from .message_report_model import MessageReport, MessageReportType
from .multi_agent_model import (
    AgentInvocation,
    AggregationStrategy,
    MultiAgentConfig,
    OrchestrationMode,
)
from .prompt_optimizer_model import (
    PromptHistory,
    PromptOptimizerSession,
    PromptOptimizerSessionHistory,
    RoleType,
)
from .release_share_model import ReleaseShare
from .skill_model import Skill
from .tool_model import (
    AuthType,
    BuiltinToolConfig,
    CustomToolConfig,
    ExecutionStatus,
    MCPSourceChannel,
    MCPToolConfig,
    ToolConfig,
    ToolExecution,
    ToolStatus,
    ToolType,
    WorkflowToolConfig,
)
from .workflow_model import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowNodeCache,
    WorkflowNodeExecution,
)

__all__ = [
    "AgentConfig",
    "AgentExecution",
    "AgentInvocation",
    "AggregationStrategy",
    "App",
    "AppAnnotation",
    "AppAnnotationHitLog",
    "AppAnnotationSetting",
    "AppRelease",
    "AppShare",
    "AppStatus",
    "AppType",
    "AppVisibility",
    "AuthType",
    "BuiltinToolConfig",
    "Conversation",
    "ConversationContextState",
    "ConversationDetail",
    "ConversationShare",
    "CustomToolConfig",
    "EndUser",
    "EndUserInfo",
    "EndUserMerge",
    "ExecutionStatus",
    "FileMetadata",
    "GenericFile",
    "HitLogSource",
    "IconType",
    "MCPSourceChannel",
    "MCPToolConfig",
    "McpMarket",
    "McpMarketConfig",
    "Message",
    "MessageFeedback",
    "MessageReport",
    "MessageReportType",
    "MultiAgentConfig",
    "OrchestrationMode",
    "PromptHistory",
    "PromptOptimizerSession",
    "PromptOptimizerSessionHistory",
    "ReleaseShare",
    "RoleType",
    "Skill",
    "ToolConfig",
    "ToolExecution",
    "ToolStatus",
    "ToolType",
    "WorkflowConfig",
    "WorkflowExecution",
    "WorkflowNodeCache",
    "WorkflowNodeExecution",
    "WorkflowToolConfig",
]
