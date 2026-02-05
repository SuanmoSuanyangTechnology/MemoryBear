/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:10 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:26:10 
 */
/**
 * Type definitions for tool configuration in application settings
 */

/**
 * Tool option for cascader selection
 */
export interface ToolOption {
  /** Option value */
  value?: string | number | null;
  /** Display label */
  label?: React.ReactNode;
  /** Tool description */
  description?: string;
  /** Child options for nested selection */
  children?: ToolOption[];
  /** Whether this is a leaf node */
  isLeaf?: boolean;
  /** Method ID for API operations */
  method_id?: string;
  /** Operation name */
  operation?: string;
  /** Method parameters */
  parameters?: Parameter[];
  /** Tool ID */
  tool_id?: string;
  /** Whether tool is enabled */
  enabled?: boolean;
}

/**
 * Parameter definition for tool methods
 */
export interface Parameter {
  /** Parameter name */
  name: string;
  /** Parameter data type */
  type: string;
  /** Parameter description */
  description: string;
  /** Whether parameter is required */
  required: boolean;
  /** Default value */
  default: any;
  /** Enum values if applicable */
  enum: null | string[];
  /** Minimum value for numeric types */
  minimum: number;
  /** Maximum value for numeric types */
  maximum: number;
  /** Regex pattern for validation */
  pattern: null | string;
}

/**
 * Modal ref for tool selection
 */
export interface ToolModalRef {
  /** Open tool selection modal */
  handleOpen: () => void;
}