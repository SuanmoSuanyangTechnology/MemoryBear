/**
 * Type definitions for Application Management
 */
import type { WorkflowConfig } from '@/views/Workflow/types';
/**
 * Search query parameters
 */
export interface Query {
  /** Search keyword */
  search_type?: string;
  search?: string;
  type?: string;
  shared_only?: boolean;
  include_shared?: boolean;
}

/**
 * Application data structure
 */
export interface Application {
  /** Application ID */
  id: string;
  /** Workspace ID */
  workspace_id: string;
  /** Creator user ID */
  created_by: string;
  /** Application name */
  name: string;
  /** Application description */
  description?: string;
  /** Icon URL */
  icon?: string;
  /** Icon type */
  icon_type?: string;
  /** Application type: agent, multi_agent, or workflow */
  type: 'agent' | 'multi_agent' | 'workflow' | 'pure_workflow';
  /** Visibility setting */
  visibility: string;
  /** Application status */
  status: string;
  /** Application tags */
  tags: string[];
  /** Current release version ID */
  current_release_id?: string;
  /** Whether application is active */
  is_active: boolean;
  /** Whether application is shared */
  is_shared: boolean;
  /** Creation timestamp */
  created_at: number;
  /** Last update timestamp */
  updated_at: number;
  share_permission?: string;
  source_workspace_name?: string;
  source_workspace_icon?: string;
  source_app_version?: string;
  source_app_is_active?: boolean;

  share_id: null | string;
  shared_by: null | string;
  shared_by_name: null | string;
  shared_at: null | number;
  ontology: {
    bound: boolean;
    is_fallback_default: false,
    ontology_id: string
    ontology_name: string
    scene_type_id: string
    scene_name: string
    custom_field_labels: string[];
    emotion_count: number;
  },
}

/**
 * Application creation/edit form data
 */
export interface ApplicationModalData {
  /** Application name */
  name: string;
  /** Application type */
  type: string;
  /** Application description */
  description?: string;
  /** Icon upload data */
  icon: {
    url: string;
    uid: string | number;
  }[];
  /** Application tags */
  tags?: string[];
}

/**
 * Application modal ref interface
 */
export interface ApplicationModalRef {
  /** Open modal with optional application data for editing */
  handleOpen: (application?: Application) => void;
}

/**
 * Model configuration modal ref interface
 */
export interface ModelConfigModalRef {
  /** Open modal with application data */
  handleOpen: (application?: Application) => void;
}

/**
 * Model configuration form data
 */
export interface ModelConfigModalData {
  /** Selected model */
  model: string;
  /** Additional configuration fields */
  [key: string]: string;
}

/**
 * AI prompt modal ref interface
 */
export interface AiPromptModalRef {
  /** Open modal with application data */
  handleOpen: (application?: Application) => void;
}

/**
 * Variable modal ref interface
 */
export interface VariableModalRef {
  /** Open modal with application data */
  handleOpen: (application?: Application) => void;
}

/**
 * Variable modal props
 */
export interface VariableModalProps {
  /** Callback to refresh variable list */
  refresh: () => void;
}

/**
 * Variable edit modal ref interface
 */
export interface VariableEditModalRef {
  /** Open modal with optional variable data */
  handleOpen: (values?: Variable) => void;
}

/**
 * Variable data structure
 */
export interface Variable {
  /** Variable index */
  index?: number;
  /** Variable type */
  type: string;
  /** Variable key */
  key: string;
  /** Variable name */
  name: string;
  /** Maximum length for string types */
  maxLength?: number;
  /** Default value */
  defaultValue?: string;
  /** Options for select type */
  options?: string[];
  /** Whether variable is required */
  required: boolean;
  /** Whether variable is hidden */
  hidden?: boolean;
}

/**
 * API extension configuration data
 */
export interface ApiExtensionModalData {
  /** Extension name */
  name: string;
  /** API endpoint URL */
  apiEndpoint: string;
  /** API authentication key */
  apiKey: string;
}

/**
 * API extension modal ref interface
 */
export interface ApiExtensionModalRef {
  /** Open API extension modal */
  handleOpen: () => void;
}

/**
 * Upload workflow modal form data
 */
export interface UploadWorkflowModalData {
  /** Platform type (e.g., 'dify') */
  platform: string;
  /** Array of uploaded files */
  file: any[];
  /** Optional workflow name */
  name?: string;
  /** Optional original file name */
  fileName?: string;
  /** Optional file size in bytes */
  fileSize?: number;
  /** Optional workflow description */
  description?: string;
}

/**
 * Complex item for errors and warnings
 */
interface ComplexItem {
  /** Error/warning type */
  type: string;
  /** Detailed error/warning message */
  detail: string;
  /** Node identifier where the error/warning occurred */
  node_id: string;
  /** Node name where the error/warning occurred */
  node_name: string;
  /** Optional scope of the error/warning */
  scope: string | null;
  /** Optional name associated with the error/warning */
  name: string | null;
}

/**
 * Upload data response
 * @extends WorkflowConfig
 */
export interface UploadData extends WorkflowConfig {
  /** Whether the upload was successful */
  success: boolean;
  /** Temporary identifier for the uploaded workflow */
  temp_id: string;
  /** Optional workflow identifier if already exists */
  workflow_id?: string;
  /** Array of error items */
  errors: ComplexItem[];
  /** Array of warning items */
  warnings: ComplexItem[];
}

/**
 * Upload workflow modal ref interface
 */
export interface UploadWorkflowModalRef {
  /** Open the upload workflow modal */
  handleOpen: () => void;
}

/**
 * Upload app modal ref interface
 */
export interface UploadModalRef {
  /** Open the upload workflow modal */
  handleOpen: () => void;
}

/**
 * 共享范围。`resource_center`=租户资源中心（共享即生效，无审核）；`community_market`=社区市场（需运营审核）。
 */
export enum Scope {
    CommunityMarket = "community_market",
    ResourceCenter = "resource_center",
}

/**
 * 共享状态，见接口描述的状态表。`resource_center` 只会是 `shared`；`unknown`
 * 表示市场不可用时的降级值，前端渲染为「状态未知」并允许点「查看记录」重试，不要当成失败。
 */
export enum ShareStatus {
    Approved = "approved",
    Pending = "pending",
    PendingSync = "pending_sync",
    Rejected = "rejected",
    Shared = "shared",
    SyncFailed = "sync_failed",
    Unknown = "unknown",
    Withdrawn = "withdrawn",
}
export interface MySharedOutItem {
    /**
     * 源应用 ID。两行共享同一个应用时值相同。
     */
    app_id: string;
    /**
     * 应用名称，取钉住的 release 快照，不是草稿。
     */
    app_name: string;
    /**
     * 应用类型，取钉住的 release 快照。前端据此渲染类型标签与图标配色。
     */
    app_type: Application['type'];
    /**
     * 资源中心条目 ID，`DELETE /apps/assets/{asset_id}` 用它取消共享。只开市场开关时条目以 `unpublished`
     * 落库做市场指针载体，此时该字段有值但 `scope` 不会出现 `resource_center` 行——**不要**因为 `asset_id` 有值就渲染资源中心行。
     */
    asset_id: null | string;
    /**
     * 编辑权限（原型开关）。`resource_center` = 条目 `share_mode == editable`；`community_market` 恒
     * `true`（市场只有复制为可编辑副本一条路径），前端应把市场行的开关置为不可点。
     */
    editable: boolean;
    /**
     * 方形图标地址；未设置时为 null。
     */
    icon: null | string;
    /**
     * 图标类型（内置图标名 / 图片），未设置时为 null。
     */
    icon_type: null | string;
    /**
     * 市场侧资源 ID。尚未在市场建条目（如 `pending_sync`）时为 null。
     */
    market_resource_id: null | string;
    /**
     * 源应用当前生效的本体（`schemas/app_schema.py::AppOntologyInfo`）。按 `app_id`
     * 解析，同一应用的两行值相同；未绑定时为系统默认本体兜底；社区版恒为 null。
     */
    ontology: Application['ontology'];
    /**
     * 待审核投稿 ID，「撤回申请」用它：`POST /external-catalog/publications/{id}/withdraw`。仅在 `share_status =
     * pending` 时有值，其余为 null——前端直接据此决定按钮是否出现，不要再按状态推一遍。
     */
    pending_publication_id: null | string;
    /**
     * 共享范围。`resource_center`=租户资源中心（共享即生效，无审核）；`community_market`=社区市场（需运营审核）。
     */
    scope: Scope;
    /**
     * 共享状态，见接口描述的状态表。`resource_center` 只会是 `shared`；`unknown`
     * 表示市场不可用时的降级值，前端渲染为「状态未知」并允许点「查看记录」重试，不要当成失败。
     */
    share_status: ShareStatus;
    /**
     * 最近一次共享 / 投稿时间，Unix 毫秒。`resource_center` 取条目最近更新时间，市场取最近一次投稿时间。
     */
    shared_at: number;
    /**
     * 该范围当前的版本号（`version_name`，如
     * `v1.0.3`）。资源中心取条目钉住的版本；市场取最新一次投稿的版本。两个范围可能不同——这是合法中间态，不要用其中一个推另一个。
     */
    version: string;
    /**
     * `version` 对应的整数序号，用于排序与「是否有新版本」比较。
     */
    version_number: number;
}