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
 * Share scope. `resource_center`=tenant resource center (effective immediately, no review); `community_market`=community market (requires operation review).
 */
export enum Scope {
    CommunityMarket = "community_market",
    ResourceCenter = "resource_center",
}

/**
 * Share status, see status table in API description. `resource_center` is always `shared`; `unknown`
 * is a fallback value when market is unavailable, frontend renders as "status unknown" and allows clicking "view records" to retry, do not treat as failure.
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
     * Source application ID. Same value when two rows share the same application.
     */
    app_id: string;
    /**
     * Application name, from pinned release snapshot, not draft.
     */
    app_name: string;
    /**
     * Application type, from pinned release snapshot. Frontend renders type label and icon color based on this.
     */
    app_type: Application['type'];
    /**
     * Resource center entry ID, `DELETE /apps/assets/{asset_id}` uses this to cancel share. When only market switch is on, entry is stored as `unpublished`
     * as market pointer carrier, at this time this field has value but `scope` will not have `resource_center` row—**do not** render resource center row just because `asset_id` has value.
     */
    asset_id: null | string;
    /**
     * Edit permission (prototype switch). `resource_center` = entry `share_mode == editable`; `community_market` is always
     * `true` (market only has copy to editable copy path), frontend should disable the switch for market rows.
     */
    editable: boolean;
    /**
     * Square icon address; null when not set.
     */
    icon: null | string;
    /**
     * Icon type (built-in icon name / image), null when not set.
     */
    icon_type: null | string;
    /**
     * Market side resource ID. Null when entry not yet created in market (e.g., `pending_sync`).
     */
    market_resource_id: null | string;
    /**
     * Currently effective ontology of source application (`schemas/app_schema.py::AppOntologyInfo`). Parsed by `app_id`,
     * same value for two rows of same application; falls back to system default ontology when not bound; always null for community edition.
     */
    ontology: Application['ontology'];
    /**
     * Pending review submission ID, "withdraw application" uses this: `POST /external-catalog/publications/{id}/withdraw`. Only has value when `share_status =
     * pending`, null otherwise—frontend directly decides button visibility based on this, do not infer from status again.
     */
    pending_publication_id: null | string;
    /**
     * Share scope. `resource_center`=tenant resource center (effective immediately, no review); `community_market`=community market (requires operation review).
     */
    scope: Scope;
    /**
     * Share status, see status table in API description. `resource_center` is always `shared`; `unknown`
     * is a fallback value when market is unavailable, frontend renders as "status unknown" and allows clicking "view records" to retry, do not treat as failure.
     */
    share_status: ShareStatus;
    /**
     * Last share / submission time, Unix milliseconds. `resource_center` takes entry last update time, market takes last submission time.
     */
    shared_at: number;
    /**
     * Current version number for this scope (`version_name`, e.g., `v1.0.3`). Resource center takes entry pinned version; market takes latest submission version. Two scopes may differ—this is a valid intermediate state, do not infer one from the other.
     */
    version: string;
    /**
     * Integer sequence number for `version`, used for sorting and comparing "has new version".
     */
    version_number: number;
    /**
     * Version number of the latest reviewed version (may differ from `version`, e.g., version under review).
     */
    review_version?: string;
    /**
     * Market listing status. `not_listed`=not listed, `listed`=listed, `pending_listing`=publisher delisted pending relist,
     * `delisted`=platform delisted. Always null for resource center rows; also null when market is unavailable.
     */
    listing_status?: 'not_listed' | 'listed' | 'pending_listing' | 'delisted' | null;
    /**
     * Delist actor. `publisher`=publisher self-delist (can self-relist); `operator`=platform delist (only platform can restore).
     * Always null for resource center rows.
     */
    delist_actor?: 'publisher' | 'operator' | null;
    /**
     * Whether delist API can be called. Determined by market, always false for resource center rows.
     */
    can_delist: boolean;
    /**
     * Whether self-relist API can be called. Only true when `listing_status=pending_listing` and `delist_actor=publisher`.
     * Always false for resource center rows.
     */
    can_relist: boolean;
}

/**
 * Community market publication event types
 */
export type PublicationEventType = 'submitted' | 'pending' | 'approved' | 'rejected' | 'withdrawn';

/**
 * Community market publication event
 */
export interface PublicationEvent {
    event_id: string;
    event_type: PublicationEventType;
    event_label: string;
    version: string;
    publication_id: string;
    occurred_at: number;
    reason: string | null;
}

/**
 * Community market publication record response
 */
export interface PublicationRecordData {
    resource_type: string;
    resource_id: string;
    market_resource_id: string | null;
    listing_status: 'listed' | 'unlisted' | null;
    current_version: string;
    can_publish: boolean;
    block_reason: string | null;
    pending_publication_id: string | null;
    events: PublicationEvent[];
}

/**
 * Application record modal ref
 */
export interface ApplicationRecordModalRef {
    handleOpen: (app_id: string) => void;
}

/**
 * Delist modal ref
 */
export interface DelistModalRef {
    handleOpen: (publication_id: string, app_name: string) => void;
}

/**
 * Relist modal ref
 */
export interface RelistModalRef {
    handleOpen: (item: MySharedOutItem) => void;
}