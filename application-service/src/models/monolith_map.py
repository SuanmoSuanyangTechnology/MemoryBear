"""Reconciliation map between this service's model modules and the monolith ORM.

The modules listed in MODEL_MODULES are frozen copies of the corresponding
`core/api/app/models/*.py` files (import rewrites plus cross-domain relationship
removal only). During the coexistence period a functional change lands in the
monolith first and is then synchronized here semantically; this map locates the
host counterpart of every module for that pass. The manifest test cross-checks
the map against the live `ServiceBase.metadata` registry.
"""

# Service model module -> class names defined in it (including enums the module
# exports for the rest of the service).
MODEL_MODULES: dict[str, tuple[str, ...]] = {
    "app_model": ("App", "AppStatus", "AppType", "AppVisibility", "IconType"),
    "app_release_model": ("AppRelease",),
    "appshare_model": ("AppShare",),
    "release_share_model": ("ReleaseShare",),
    "agent_app_config_model": ("AgentConfig",),
    "agent_execution_model": ("AgentExecution",),
    "multi_agent_model": (
        "AgentInvocation",
        "AggregationStrategy",
        "MultiAgentConfig",
        "OrchestrationMode",
    ),
    "workflow_model": (
        "WorkflowConfig",
        "WorkflowExecution",
        "WorkflowNodeCache",
        "WorkflowNodeExecution",
    ),
    "conversation_model": (
        "Conversation",
        "ConversationContextState",
        "ConversationDetail",
        "Message",
    ),
    "conversation_share_model": ("ConversationShare",),
    "tool_model": (
        "AuthType",
        "BuiltinToolConfig",
        "CustomToolConfig",
        "ExecutionStatus",
        "MCPSourceChannel",
        "MCPToolConfig",
        "ToolConfig",
        "ToolExecution",
        "ToolStatus",
        "ToolType",
        "WorkflowToolConfig",
    ),
    "skill_model": ("Skill",),
    "mcp_market_model": ("McpMarket",),
    "mcp_market_config_model": ("McpMarketConfig",),
    "annotation_model": (
        "AppAnnotation", "AppAnnotationHitLog", "AppAnnotationSetting", "HitLogSource"
    ),
    "message_feedback_model": ("MessageFeedback",),
    "message_report_model": ("MessageReport", "MessageReportType"),
    "end_user_model": ("EndUser", "EndUserMerge"),
    "end_user_info_model": ("EndUserInfo",),
    "prompt_optimizer_model": (
        "PromptHistory",
        "PromptOptimizerSession",
        "PromptOptimizerSessionHistory",
        "RoleType",
    ),
    "generic_file_model": ("GenericFile",),
    "file_metadata_model": ("FileMetadata",),
}

# Service model module -> monolith source file (repository-relative).
MONOLITH_PATHS: dict[str, str] = {
    module: f"core/api/app/models/{module}.py" for module in MODEL_MODULES
}
