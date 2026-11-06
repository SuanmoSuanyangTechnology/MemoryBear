export interface AppAssetsQuery {
    /**
     * 按功能分类过滤，单值。命中该应用 `categories[]` 中任一元素即匹配。取值见方案 §4.5 的 8 项枚举。
     */
    category?: string;
    /**
     * 页码，从 1 开始。
     */
    page?: number;
    /**
     * 每页条数，默认 20，最大 100。
     */
    pagesize?: number;
    /**
     * 关键词，匹配应用名称、描述与发布人名称（对应截图搜索框「搜索应用名称、描述或发布人」）。
     */
    q?: string;
    /**
     * 标签过滤，逗号分隔，命中任一即可。
     */
    tags?: string;
    /**
     * 应用类型过滤：agent | workflow | multi_agent。
     */
    type?: string;
}
export interface AppAssetsResponse {
    items: AppAssetItem[];
    page: Page;
}

export interface AppAssetItem {
    /**
     * 功能分类，1~5 项，取值来自固定枚举（见方案 §4.5）。随版本冻结，不是运营临时补。
     */
    categories: Category[];
    /**
     * 社区市场用的横版封面地址；不是卡片图标，图标见 icon。
     */
    cover?: string;
    /**
     * Unix 毫秒。
     */
    created_at?: number;
    /**
     * 条目创建者用户 ID（分享方）。
     */
    created_by?: string;
    /**
     * 应用描述，**创建应用时填写的描述**（随版本快照冻结）。列表卡片描述区文案。
     */
    description?: string;
    /**
     * 源应用是否已有更新版本。共享跟版本走：源应用发新版不会自动前移，只在此提示。
     */
    has_newer_version?: boolean;
    /**
     * 应用图标地址，卡片左侧方形图标。可为空，空时前端按 source_app_type 兜底。
     */
    icon?: null | string;
    /**
     * 图标类型：locale | remote。
     */
    icon_type?: null | string;
    /**
     * 资源中心条目 ID。
     */
    id: string;
    /**
     * 被拉取次数：有多少人把它添加到自己的空间。
     */
    install_count: number;
    /**
     * 已提交社区市场时的市场资源 ID；未提交为 null。
     */
    market_resource_id?: string;
    /**
     * 条目钉住的版本；消费方跟这个走。
     */
    published_release_id: string;
    /**
     * 条目钉住的版本号，冗余字段，免 join。
     */
    published_version: number;
    /**
     * 发布人展示名，卡片上「发布人」旁的文案。服务端按 created_by join 用户取展示名。
     */
    publisher_name: string;
    /**
     * 共享模式：readonly（只读引用）/ editable（可复制为独立副本）。决定 install 的行为，调用方无法提权。
     */
    share_mode: ShareMode;
    /**
     * 源应用 ID。
     */
    source_app_id: string;
    /**
     * 应用名称，卡片标题。
     */
    source_app_name: string;
    /**
     * 应用类型：agent | workflow | multi_agent。卡片图标按此着色（agent 蓝 / workflow 蓝链 / multi_agent 绿群）。
     */
    source_app_type: SourceAppType;
    /**
     * 派生值：源应用 latest 版本号。与 published_version 比对得出 has_newer_version。
     */
    source_app_version?: string;
    /**
     * 条目上架时快照的来源空间 ID。
     */
    source_workspace_id?: string;
    /**
     * 来源空间名称，副标题「来自哪个空间」。
     */
    source_workspace_name?: string;
    /**
     * 条目状态：draft | published | unpublished。列表只返回 published。
     */
    status: Status;
    /**
     * 标签数组。
     */
    tags?: string[];
    /**
     * Unix 毫秒。
     */
    updated_at?: number;
    /**
     * 浏览次数：被多少人看过。卡片上眼睛图标后的数字。与 install_count 是两个不同指标。
     */
    view_count: number;
}

export interface Categories {
    /**
     * 分类 code（英文短码），稳定标识。投稿时作为 `display.categories` 的值。
     */
    code: string;
    /**
     * 中文展示名。
     */
    name: string;
    /**
     * 英文展示名，仅用于 en 界面。不参与校验与匹配。
     */
    name_en: string;
    /**
     * 排序，升序。前端按此渲染，不硬编码顺序。
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
 * 应用类型：agent | workflow | multi_agent。卡片图标按此着色（agent 蓝 / workflow 蓝链 / multi_agent 绿群）。
 */
export enum SourceAppType {
    Agent = "agent",
    MultiAgent = "multi_agent",
    Workflow = "workflow",
}

/**
 * 条目状态：draft | published | unpublished。列表只返回 published。
 */
export enum Status {
    Draft = "draft",
    Published = "published",
    Unpublished = "unpublished",
}

export interface Page {
    /**
     * 是否有下一页
     */
    hasnext: boolean;
    /**
     * 当前页码
     */
    page: number;
    /**
     * 每页条数
     */
    pagesize: number;
    /**
     * 符合条件的总记录数
     */
    total: number;
}

export interface AppAssetDetail {
    /**
     * 功能分类，1~5 项，取值来自固定枚举（见方案 §4.5）。随版本冻结，不是运营临时补。
     */
    categories: Category[];
    /**
     * 更新说明，取条目钉住版本的 `app_releases.release_notes`，随版本冻结。为空时前端渲染「暂无更新说明」。
     */
    changelog: null | string;
    cover?: string;
    /**
     * Unix 毫秒。
     */
    created_at?: number;
    created_by?: string;
    /**
     * 应用描述，**创建应用时填写的描述**（`apps.description`，发布时冻结进
     * `app_releases.description`）。详情页名称下方那段正文就渲染这个字段。空值前端不渲染该行。
     */
    description: string;
    /**
     * 完整配置快照。同租户内不脱敏。agent 含 system_prompt / model_config_ref / tools / skills /
     * knowledge_retrieval；multi_agent 另含 sub_agents / routing_rules。
     */
    display_payload: { [key: string]: any };
    detail_view?: {
      kind: string;
      workflow?: Record<string, unknown> | null;
      cluster?: Record<string, unknown> | null;
    }; 
    has_newer_version?: boolean;
    /**
     * 应用图标地址，详情页左上角方形图标。可为空，空时前端按 source_app_type 兜底。
     */
    icon: null | string;
    /**
     * 图标类型：locale | remote。
     */
    icon_type: null | string;
    id: string;
    /**
     * 拉取情况汇总（分享方视角）。
     */
    installations_summary?: InstallationsSummary;
    /**
     * 该版本实际引用到的技能包（来自钉住版本的快照，不是 resource_mapping）。空数组渲染「未关联」。
     */
    linked_skills: LinkedSkill[];
    /**
     * 该版本实际引用到的工具（来自钉住版本的快照，不是 resource_mapping）。空数组渲染「未关联」。
     */
    linked_tools: LinkedTool[];
    published_release_id: string;
    published_version?: number;
    published_version_name?: string;
    /**
     * 发布人展示名，详情页名称下方「发布人」。服务端按 created_by join 用户取展示名。
     */
    publisher_name: string;
    /**
     * 空间级资源映射结果。同租户内模型/工具/skill 原样复用 id；知识库与记忆按空间重映射。
     */
    resource_mapping?: ResourceMapping[];
    share_mode: ShareMode;
    source_app_id: string;
    source_app_name?: string;
    source_app_type?: SourceAppType;
    /**
     * 源应用最新版本号（派生值）。
     */
    source_app_version?: string;
    source_workspace_id?: string;
    source_workspace_name?: string;
    status: Status;
    tags?: string[];
    /**
     * Unix 毫秒。
     */
    updated_at?: number;
    /**
     * 使用示例，随版本冻结。**纯字符串数组，按录入顺序返回**，不暴露 order/content 落库结构。空数组表示未录入，前端不渲染该区块。
     */
    usage_examples: string[];
    /**
     * 浏览次数：打开详情页次数，详情页眼睛图标后的数字。与 installations_summary 不是同一指标。
     */
    view_count: number;
}

/**
 * 拉取情况汇总（分享方视角）。
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
     * 在本空间映射到的资源 ID；未映射为 null。
     */
    local_id?: string;
    local_name?: string;
    ref_name?: string;
    /**
     * 是否已自动命中。
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