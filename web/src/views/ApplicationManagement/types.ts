/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:15 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-06 11:08:37
 */
/**
 * Type definitions for Application Management
 */

/**
 * Search query parameters
 */
export interface Query {
  /** Search keyword */
  search: string;
  type?: string;
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
  type: 'agent' | 'multi_agent' | 'workflow';
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


export interface UploadWorkflowModalData {
}
export interface UploadWorkflowModalRef {
  handleOpen: () => void;
}