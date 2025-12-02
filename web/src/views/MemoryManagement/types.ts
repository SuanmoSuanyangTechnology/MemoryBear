// 内存管理表单数据类型
export interface MemoryFormData {
  config_id?: number;
  config_name: string;
  config_desc?: string;
}

  // 内存数据类型
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
  lambda_mem: string;
  offset: string;
  state: boolean;
  created_at: string;
  updated_at: string;
  config_desc: string;
  workspace_id: string;
  [key: string]: string | number | boolean;
}
// 定义组件暴露的方法接口
export interface MemoryFormRef {
  handleOpen: (memory?: Memory | null) => void;
}