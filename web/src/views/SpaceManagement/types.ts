/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:51 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:48:51 
 */
/**
 * Application data type
 */

/** Storage type */
export type StorageType = 'rag' | 'neo4j';

/**
 * Space data structure
 */
export interface Space {
  id: string;
  name: string;
  description?: string;
  tenant_id: string;
  created_at: string | number;
  is_active: boolean;
  icon: string;
  storage_type: StorageType | null;
}

/**
 * Create form data type
 */
export interface SpaceModalData {
  name: string;
  type: string;
  icon?: any;
  iconType?: 'remote';
  llm: string;
  embedding: string;
  rerank: string;
  storage_type: StorageType;
}

/**
 * Component exposed methods interface
 */
export interface SpaceModalRef {
  handleOpen: (space?: Space) => void;
}
export interface ModelConfigModalRef {
  handleOpen: (space?: Space) => void;
}
export interface ModelConfigModalData {
  model: string;
  [key: string]: string;
}
export interface AiPromptModalRef {
  handleOpen: (space?: Space) => void;
}
export interface VariableModalRef {
  handleOpen: (space?: Space) => void;
}
export interface VariableModalProps {
  refresh: () => void;
}
export interface VariableEditModalRef {
  handleOpen: (values?: Variable) => void;
}
export interface Variable {
  index?: number;
  type: string;
  key: string;
  name: string;
  maxLength?: number;
  defaultValue?: string;
  options?: string[];
  required: boolean;
  hidden?: boolean;
}
export interface ApiExtensionModalData {
  name: string;
  apiEndpoint: string;
  apiKey: string;
}
export interface ApiExtensionModalRef {
  handleOpen: () => void;
}
