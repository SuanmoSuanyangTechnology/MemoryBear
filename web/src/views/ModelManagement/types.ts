/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:18 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:50:18 
 */
/**
 * Type definitions for Model Management
 */

/**
 * Query parameters for model filtering
 */
export interface Query {
  /** Model type filter */
  type?: string;
  /** Model provider filter */
  provider?: string;
  /** Active status filter */
  is_active?: boolean;
  /** Public status filter */
  is_public?: boolean;
  /** Composite model filter */
  is_composite?: boolean;
  /** Search keyword */
  search?: string;
  /** Page size */
  pagesize?: number;
  /** Page number */
  page?: number;
}

/**
 * Description item for model details
 */
export interface DescriptionItem {
  /** Item key */
  key: string;
  /** Item label */
  label: string;
  /** Item content */
  children: string;
}

/**
 * Composite model form data
 */
export interface CompositeModelForm {
  /** Model logo */
  logo?: any;
  /** Model name */
  name: string;
  /** Model type */
  type?: string;
  /** Model description */
  description: string;
  /** Associated API key IDs */
  api_key_ids: ModelApiKey[] | string[];
}

/**
 * Group model modal ref interface
 */
export interface GroupModelModalRef {
  /** Open modal with optional model data */
  handleOpen: (model?: ModelListItem) => void;
}

/**
 * Group model modal props
 */
export interface GroupModelModalProps {
  /** Callback to refresh model list */
  refresh?: () => void;
}

/**
 * Model list detail ref interface
 */
export interface ModelListDetailRef {
  /** Open detail drawer with provider model data */
  handleOpen: (vo: ProviderModelItem) => void;
}

/**
 * Model API key configuration
 */
export interface ModelApiKey {
  /** Model name */
  model_name: string;
  /** API key description */
  description: string | null;
  /** Model provider */
  provider: string;
  /** API key value */
  api_key: string;
  /** API base URL */
  api_base: string;
  /** Additional configuration */
  config: any;
  /** Whether API key is active */
  is_active: boolean;
  /** Priority level */
  priority: string;
  /** API key ID */
  id: string;
  /** Usage count */
  usage_count: string;
  /** Last used timestamp */
  last_used_at: number;
  /** Creation timestamp */
  created_at: number;
  /** Update timestamp */
  updated_at: number;
  /** Associated model config IDs */
  model_config_ids: string[];
}

/**
 * Model list item data structure
 */
export interface ModelListItem {
  /** Model name */
  model_name?: string;
  /** Associated model config IDs */
  model_config_ids: string[];
  /** Display name */
  name: string;
  /** Model type */
  type: string;
  /** Model logo URL */
  logo: string;
  /** Model description */
  description: string;
  /** Model provider */
  provider: string;
  /** Model configuration */
  config: any;
  /** Whether model is active */
  is_active: boolean;
  /** Whether model is public */
  is_public: boolean;
  /** Model ID */
  id: string;
  /** Creation timestamp */
  created_at: number;
  /** Update timestamp */
  updated_at: number;
  /** Associated API keys */
  api_keys: ModelApiKey[]
}

/**
 * Provider model item grouping
 */
export interface ProviderModelItem {
  /** Provider name */
  provider: string;
  /** Provider logo URL */
  logo?: string;
  /** Provider tags */
  tags: string[];
  /** Models from this provider */
  models: ModelListItem[];
}

/**
 * Key configuration modal form data
 */
export interface KeyConfigModalForm {
  /** Model provider */
  provider: string;
  /** API key value */
  api_key: string;
  /** API base URL */
  api_base: string;
}

/**
 * Key configuration modal ref interface
 */
export interface KeyConfigModalRef {
  /** Open modal with provider model data */
  handleOpen: (vo: ProviderModelItem) => void;
}

/**
 * Key configuration modal props
 */
export interface KeyConfigModalProps {
  /** Callback to refresh model list */
  refresh?: () => void;
}

/**
 * Multi-key configuration form data
 */
export interface MultiKeyForm {
  /** Model config ID */
  model_config_id?: string;
  /** Model name */
  model_name: string;
  /** Model provider */
  provider: string;
  /** API key value */
  api_key: string;
  /** API base URL */
  api_base: string;
}

/**
 * Multi-key configuration modal ref interface
 */
export interface MultiKeyConfigModalRef {
  /** Open modal with model data */
  handleOpen: (vo: ModelListItem, provider?: string) => void;
}

/**
 * Multi-key configuration modal props
 */
export interface MultiKeyConfigModalProps {
  /** Callback to refresh model list */
  refresh?: () => void;
}

/**
 * Model plaza grouping by provider
 */
export interface ModelPlaza {
  /** Provider name */
  provider: string;
  /** Models from this provider */
  models: ModelPlazaItem[];
}

/**
 * Model plaza item data structure
 */
export interface ModelPlazaItem {
  /** Model ID */
  id: string;
  /** Model name */
  name: string;
  /** Model type */
  type: string;
  /** Model provider */
  provider: string;
  /** Model logo URL */
  logo: string;
  /** Model description */
  description: string;
  /** Whether model is deprecated */
  is_deprecated: boolean;
  /** Whether model is official */
  is_official: boolean;
  /** Model tags */
  tags: string[];
  /** Number of times added */
  add_count: number;
  /** Whether user has added this model */
  is_added: boolean;
}

/**
 * Model square detail ref interface
 */
export interface ModelSquareDetailRef {
  /** Open detail drawer with model plaza data */
  handleOpen: (vo: ModelPlaza) => void;
}

/**
 * Custom model form data
 */
export interface CustomModelForm {
  /** Model name */
  name: string;
  /** Model type */
  type?: string;
  /** Model provider */
  provider?: string;
  /** Model logo */
  logo?: any;
  /** Model description */
  description: string;
  /** Whether model is official */
  is_official: boolean;
  /** Model tags */
  tags: string[];
}

/**
 * Custom model modal ref interface
 */
export interface CustomModelModalRef {
  /** Open modal with optional model plaza item */
  handleOpen: (vo?: ModelPlazaItem) => void;
}

/**
 * Custom model modal props
 */
export interface CustomModelModalProps {
  /** Callback to refresh model list */
  refresh?: () => void;
}

/**
 * Base ref interface for list components
 */
export interface BaseRef {
  /** Refresh list data */
  getList: () => void;
}