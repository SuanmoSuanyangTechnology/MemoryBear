import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types'
import type { ChatItem } from '@/components/Chat/types'
import type { GraphRef } from '@/views/Workflow/types';
import type { ApiKey } from '@/views/ApiKeyManagement/types'

export interface ModelConfig {
  label?: string;
  default_model_config_id?: string;
  temperature: number;
  max_tokens: number;
  top_p: number;
  frequency_penalty: number;
  presence_penalty: number;
  n: number;
  stop?: string;
}

/*************** 知识库相关 ******************/
export interface RerankerConfig {
  rerank_model?: boolean | undefined;
  reranker_id?: string | undefined;
  reranker_top_k?: number | undefined;
}
export interface KnowledgeConfigForm {
  kb_id?: string;
  similarity_threshold?: number;
  vector_similarity_weight?: number;
  top_k?: number;
  retrieve_type?: 'participle' | 'semantic' | 'hybrid';
}
export interface KnowledgeBase extends KnowledgeBaseListItem, KnowledgeConfigForm {
  config?: KnowledgeConfigForm
}
export interface KnowledgeConfig extends RerankerConfig {
  knowledge_bases: KnowledgeBase[];
}

export interface KnowledgeConfigModalRef {
  handleOpen: (data: KnowledgeBase) => void;
}
export interface KnowledgeGlobalConfigModalRef {
  handleOpen: () => void;
}
/*********** end 知识库相关 ******************/

/*************** 变量相关 ******************/
export interface Variable {
  index?: number;
  name: string;
  display_name: string;
  type: string;
  required: boolean;
  max_length?: number;
  description?: string;

  key: string;
  default_value?: string;
  options?: string[];
  api_extension?: string;
  hidden?: boolean;
}
export interface VariableEditModalRef {
  handleOpen: (values?: Variable) => void;
}
/*********** end 变量相关 ******************/
export interface MemoryConfig {
  enabled: boolean;
  memory_content?: string;
  max_history?: number | string;
}

export interface Config extends MultiAgentConfig {
  id: string;
  app_id: string;
  system_prompt: string;
  default_model_config_id?: string;
  model_parameters: ModelConfig;
  knowledge_retrieval: KnowledgeConfig | null;
  memory?: MemoryConfig;
  variables: Variable[];
  tools: {
    web_search: {
      enabled: boolean;
      config: {
        web_search: boolean;
      }
    }
  };
  is_active: boolean;
  created_at: number;
  updated_at: number;
}
export interface MultiAgentConfig {
  id: string;
  app_id: string;
  // system_prompt: string;
  // default_model_config_id?: string;
  // model_parameters: ModelConfig;
  // knowledge_retrieval: KnowledgeConfig | null;
  // memory?: MemoryConfig;
  // variables: Variable[];
  // tools: Record<string, string>;
  // is_active: boolean;
  // created_at: number;
  // updated_at: number;
  master_agent_id?: string;
  sub_agents?: SubAgentItem[];
}

// 创建表单数据类型
export interface ApplicationModalData {
  name: string;
  type: string;
  icon: string;
}

// 定义组件暴露的方法接口
export interface AgentRef {
  handleSave: (flag?: boolean) => Promise<any>;
}
export interface ClusterRef {
  handleSave: (flag?: boolean) => Promise<any>;
}
export interface WorkflowRef {
  handleSave: (flag?: boolean) => Promise<any>;
  handleRun: () => void;
  graphRef: GraphRef
}
export interface ApplicationModalRef {
  handleOpen: (application?: Config) => void;
}
export interface ModelConfigModalRef {
  handleOpen: (source: 'chat' | 'model') => void;
}
export interface ModelConfigModalData {
  model: string;
  [key: string]: string;
}
export interface AiPromptModalRef {
  handleOpen: () => void;
}
export interface KnowledgeModalRef {
  handleOpen: (config?: KnowledgeConfig[]) => void;
}
export interface ApiExtensionModalData {
  name: string;
  apiEndpoint: string;
  apiKey: string;
}
export interface ApiExtensionModalRef {
  handleOpen: () => void;
}
export interface ChatData {
  label?: string;
  model_config_id?: string;
  model_parameters?: ModelConfig;
  list?: ChatItem[];
  conversation_id?: string | null;
}
export interface Release {
  id: string;
  app_id: string;
  version: string;
  release_notes: string;
  name: string;
  description?: string;
  icon: string;
  icon_type?: string;
  type: string;
  visibility: string;
  config: Config;
  default_model_config_id?: string;
  published_by?: string;
  published_at: number;
  publisher_name?: string;
  is_active?: boolean;
  created_at?: number;
  updated_at?: number;
  status?: string;
  version_name?: string;
  tagKey: 'current' | 'rolledBack' | 'history';
}
export interface ReleaseModalRef {
  handleOpen: () => void;
}
export interface ReleaseShareModalRef {
  handleOpen: () => void;
}
export interface CopyModalRef {
  handleOpen: () => void;
}
export interface SubAgentItem {
  agent_id: string;
  name: string;
  role: string;
  capabilities: string[];
}
export interface SubAgentModalRef {
  handleOpen: (agent?: SubAgentItem) => void;
}
export interface ApiKeyModalRef {
  handleOpen: () => void;
}
export interface ApiKeyConfigModalRef {
  handleOpen: (apiKey: ApiKey) => void;
}
export interface AiPromptVariableModalRef {
  handleOpen: () => void;
}

export interface AiPromptForm {
  model_id?: string;
  message?: string;
  current_prompt?: string;
}