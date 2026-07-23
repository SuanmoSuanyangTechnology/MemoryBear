/**
 * Release / version publishing related types.
 */
import type { Config } from './config'

/**
 * Release version data
 */
export interface Release {
  /** Release ID */
  id: string;
  /** Application ID */
  app_id: string;
  /** Version number */
  version: string;
  /** Release notes */
  release_notes: string;
  /** Release name */
  name: string;
  /** Release description */
  description?: string;
  /** Application icon */
  icon: string;
  /** Icon type */
  icon_type?: string;
  /** Application type */
  type: string;
  /** Visibility setting */
  visibility: string;
  /** Configuration snapshot */
  config: Config;
  /** Default model configuration ID */
  default_model_config_id?: string;
  /** Publisher user ID */
  published_by?: string;
  /** Publication timestamp */
  published_at: number;
  /** Publisher name */
  publisher_name?: string;
  /** Whether release is active */
  is_active?: boolean;
  /** Creation timestamp */
  created_at?: number;
  /** Last update timestamp */
  updated_at?: number;
  /** Release status */
  status?: string;
  /** Version name */
  version_name?: string;
  /** Tag key for UI display */
  tagKey: 'current' | 'rolledBack' | 'history';
}

export interface ReleaseModalData {
  version_name: string
  release_notes: string
  icon?: any;
  name?: string;
}

/**
 * Release modal ref methods
 */
export interface ReleaseModalRef {
  /** Open release modal */
  handleOpen: () => void;
}

/**
 * Release share modal ref methods
 */
export interface ReleaseShareModalRef {
  /** Open release share modal */
  handleOpen: () => void;
}
