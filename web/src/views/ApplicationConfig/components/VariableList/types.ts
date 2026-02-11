/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:21 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:26:21 
 */
/**
 * Type definitions for variable configuration in application settings
 */

/**
 * Variable definition for application input
 */
export interface Variable {
  /** Variable index in list */
  index?: number;
  /** Variable name (identifier) */
  name: string;
  /** Display name shown to users */
  display_name: string;
  /** Variable data type (string, number, select, etc.) */
  type: string;
  /** Whether variable is required */
  required: boolean;
  /** Maximum length for string types */
  max_length?: number;
  /** Variable description */
  description?: string;
  /** Unique key for React rendering */
  key?: string;
  /** Default value */
  default_value?: string;
  /** Options for select type variables */
  options?: string[];
  /** API extension for dynamic options */
  api_extension?: string;
  /** Whether variable is hidden from UI */
  hidden?: boolean;
  /** Current value */
  value?: any;
}

/**
 * Modal ref for variable editing
 */
export interface VariableEditModalRef {
  /** Open modal with optional existing variable data */
  handleOpen: (values?: Variable) => void;
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
 * Modal ref for API extension configuration
 */
export interface ApiExtensionModalRef {
  /** Open API extension modal */
  handleOpen: () => void;
}