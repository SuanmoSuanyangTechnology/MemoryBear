export interface AppAssetsQuery {
    /**
     * Filter by function category, single value. Matches if any element in application `categories[]` matches. Values from 8-item enum in spec §4.5.
     */
    category?: string;
    /**
     * Page number, starting from 1.
     */
    page?: number;
    /**
     * Items per page, default 20, max 100.
     */
    pagesize?: number;
    /**
     * Keyword, matches application name, description and publisher name (corresponds to search box "Search application name, description or publisher" in screenshot).
     */
    q?: string;
    /**
     * Tag filter, comma-separated, matches any one.
     */
    tags?: string;
    /**
     * Application type filter: agent | workflow | pure_workflow | multi_agent.
     */
    type?: string;
}
export interface AppAssetsResponse {
    items: AppAssetItem[];
    page: Page;
}

export interface AppAssetItem {
    /**
     * Function categories, 1~5 items, values from fixed enum (see spec §4.5). Frozen with version, not added by operations temporarily.
     */
    categories: Category[];
    /**
     * Horizontal cover image URL for community market; not card icon, icon see `icon`.
     */
    cover?: string;
    /**
     * Unix milliseconds.
     */
    created_at?: number;
    /**
     * Entry creator user ID (sharer).
     */
    created_by?: string;
    /**
     * Application description, **description filled when creating application** (frozen with version snapshot). List card description area text.
     */
    description?: string;
    /**
     * Whether source application has newer version. Share follows version: source application releasing new version does not automatically advance, only prompts here.
     */
    has_newer_version?: boolean;
    /**
     * Application icon URL, square icon on left side of card. Can be empty, when empty frontend falls back to `source_app_type`.
     */
    icon?: null | string;
    /**
     * Icon type: locale | remote.
     */
    icon_type?: null | string;
    /**
     * Resource center entry ID.
     */
    id: string;
    /**
     * Pull count: how many people added it to their workspace.
     */
    install_count: number;
    /**
     * Market resource ID when submitted to community market; null when not submitted.
     */
    market_resource_id?: string;
    /**
     * Entry pinned version; consumer follows this.
     */
    published_release_id: string;
    /**
     * Entry pinned version number, redundant field to avoid join.
     */
    published_version: number;
    /**
     * Publisher display name, text next to "publisher" on card. Server joins user by `created_by` to get display name.
     */
    publisher_name: string;
    /**
     * Share mode: readonly (read-only reference) / editable (can copy as independent copy). Determines install behavior, caller cannot escalate permission.
     */
    share_mode: ShareMode;
    /**
     * Source application ID.
     */
    source_app_id: string;
    /**
     * Application name, card title.
     */
    source_app_name: string;
    /**
     * Application type: agent | workflow | pure_workflow | multi_agent. Card icon is colored by this (agent blue / workflow, pure_workflow blue chain / multi_agent green group).
     */
    source_app_type: SourceAppType;
    /**
     * Derived value: source application latest version number. Compared with `published_version` to get `has_newer_version`.
     */
    source_app_version?: string;
    /**
     * Source workspace ID snapshot when entry listed.
     */
    source_workspace_id?: string;
    /**
     * Source workspace name, subtitle "from which workspace".
     */
    source_workspace_name?: string;
    /**
     * Entry status: draft | published | unpublished. List only returns published.
     */
    status: Status;
    /**
     * Tag array.
     */
    tags?: string[];
    /**
     * Unix milliseconds.
     */
    updated_at?: number;
    /**
     * View count: how many people viewed it. Number after eye icon on card. Different metric from `install_count`.
     */
    view_count: number;
}

export interface Categories {
    /**
     * Category code (English short code), stable identifier. Used as `display.categories` value when submitting.
     */
    code: string;
    /**
     * Chinese display name.
     */
    name: string;
    /**
     * English display name, only used for en interface. Not involved in validation and matching.
     */
    name_en: string;
    /**
     * Sort order, ascending. Frontend renders by this, do not hardcode order.
     */
    sort: number;
}
export enum Category {
    会议协作 = "会议协作",
    内容创作 = "内容创作",
    办公提效 = "办公提效",
    数据分析 = "数据分析",
    知识检索 = "知识检索",
    研发编程 = "研发编程",
    调研分析 = "调研分析",
    销售支持 = "销售支持",
}

/**
 * Share mode: readonly (read-only reference) / editable (can be copied as an independent copy).
 * Determines the behavior of install; callers cannot escalate permissions.
 */
export enum ShareMode {
    Editable = "editable",
    Readonly = "readonly",
}

/**
 * Application type: agent | workflow | multi_agent. Card icon is colored by this (agent blue / workflow blue chain / multi_agent green group).
 */
export enum SourceAppType {
    Agent = "agent",
    MultiAgent = "multi_agent",
    Workflow = "workflow",
    PureWorkflow = "pure_workflow",
}

/**
 * Item status: draft | published | unpublished. List only returns published.
 */
export enum Status {
    Draft = "draft",
    Published = "published",
    Unpublished = "unpublished",
}

export interface Page {
    /**
     * Whether there is a next page.
     */
    hasnext: boolean;
    /**
     * Current page number.
     */
    page: number;
    /**
     * Items per page.
     */
    pagesize: number;
    /**
     * Total number of records matching the criteria.
     */
    total: number;
}

export interface AppAssetDetail {
    /**
     * Function categories, 1~5 items, values from fixed enum (see spec §4.5). Frozen with version, not added by operations temporarily.
     */
    categories: Category[];
    /**
     * Changelog, from pinned version's `app_releases.release_notes`, frozen with version. When empty, frontend renders "No changelog".
     */
    changelog: null | string;
    cover?: string;
    /**
     * Unix milliseconds.
     */
    created_at?: number;
    created_by?: string;
    /**
     * Application description, **description filled when creating application** (`apps.description`, frozen into `app_releases.description` at publish). Detail page renders this field as body text below name. When empty, frontend does not render this row.
     */
    description: string;
    /**
     * Complete configuration snapshot. Not desensitized within same tenant. agent includes system_prompt / model_config_ref / tools / skills / knowledge_retrieval; multi_agent additionally includes sub_agents / routing_rules.
     */
    display_payload: { [key: string]: any };
    detail_view?: {
      kind: string;
      workflow?: Record<string, unknown> | null;
      cluster?: Record<string, unknown> | null;
    }; 
    has_newer_version?: boolean;
    /**
     * Application icon URL, square icon at top-left of detail page. Can be empty, when empty frontend falls back to `source_app_type`.
     */
    icon: null | string;
    /**
     * Icon type: locale | remote.
     */
    icon_type: null | string;
    id: string;
    /**
     * Installation summary (sharer's perspective).
     */
    installations_summary?: InstallationsSummary;
    /**
     * Skills actually referenced by this version (from pinned version snapshot, not resource_mapping). Empty array renders "Not linked".
     */
    linked_skills: LinkedSkill[];
    /**
     * Tools actually referenced by this version (from pinned version snapshot, not resource_mapping). Empty array renders "Not linked".
     */
    linked_tools: LinkedTool[];
    published_release_id: string;
    published_version?: number;
    published_version_name?: string;
    /**
     * Publisher display name, "publisher" below name on detail page. Server joins user by `created_by` to get display name.
     */
    publisher_name: string;
    /**
     * Workspace-level resource mapping result. Models/tools/skill reuse id as-is within same tenant; knowledge bases and memory are remapped by workspace.
     */
    resource_mapping?: ResourceMapping[];
    share_mode: ShareMode;
    source_app_id: string;
    source_app_name?: string;
    source_app_type?: SourceAppType;
    /**
     * Source application latest version number (derived value).
     */
    source_app_version?: string;
    source_workspace_id?: string;
    source_workspace_name?: string;
    status: Status;
    tags?: string[];
    /**
     * Unix milliseconds.
     */
    updated_at?: number;
    /**
     * Usage examples, frozen with version. **Pure string array, returned in entry order**, does not expose order/content storage structure. Empty array means not entered, frontend does not render this section.
     */
    usage_examples: string[];
    setup_steps: string;
    /**
     * View count: number of times detail page opened, number after eye icon on detail page. Different metric from `installations_summary`.
     */
    view_count: number;
}

/**
 * Installation summary (sharer's perspective).
 */
export interface InstallationsSummary {
    copy?: number;
    reference?: number;
    total: number;
}

export interface LinkedSkill {
    name: string;
    skill_type: 'skill' | 'plugin';
}

export interface LinkedTool {
    name: string;
    /**
     * builtin | mcp | custom | workflow
     */
    tool_type: string;
    icon?: string | null;
}

export interface ResourceMapping {
    kind?: Kind;
    /**
     * Resource ID mapped in this workspace; null when not mapped.
     */
    local_id?: string;
    local_name?: string;
    ref_name?: string;
    /**
     * Whether automatically matched.
     */
    resolved?: boolean;
}

export enum Kind {
    KnowledgeBase = "knowledge_base",
    Memory = "memory",
    Model = "model",
    Skill = "skill",
    Tool = "tool",
}