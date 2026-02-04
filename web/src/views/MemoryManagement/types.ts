/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:33:01 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-03 17:33:24
 */
/**
 * Memory management form data type
 */
export interface MemoryFormData {
  config_id?: number;
  config_name: string;
  config_desc?: string;
  scene_id?: string;
}

/**
 * Memory configuration data type
 */
export interface Memory {
  config_id: number;
  config_name: string;
  group_id: string;
  user_id: string;
  apply_id: string;
  enable_llm_dedup_blockwise: boolean;
  enable_llm_disambiguation: boolean;
  deep_retrieval: boolean;
  t_type_strict: string;
  t_name_strict: string;
  t_overall: string;
  chunker_strategy: string;
  statement_granularity: string;
  include_dialogue_context: boolean;
  max_context: string;
  lambda_mem: string;
  offset: string;
  state: boolean;
  created_at: string;
  updated_at: string;
  config_desc: string;
  workspace_id: string;
  scene_id: string;
  scene_name: string;
  [key: string]: string | number | boolean;
}
/**
 * Component exposed methods interface
 */
export interface MemoryFormRef {
  handleOpen: (memory?: Memory | null) => void;
}