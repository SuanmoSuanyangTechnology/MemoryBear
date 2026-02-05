/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:29:49 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-05 10:31:10
 */
import type { KnowledgeConfig } from './components/Knowledge/types'
import type { Variable } from './components/VariableList/types'
import type { ToolOption } from './components/ToolList/types'
import type { ChatItem } from '@/components/Chat/types'
import type { GraphRef } from '@/views/Workflow/types';
import type { ApiKey } from '@/views/ApiKeyManagement/types'
import type { SkillConfigForm } from './components/Skill/types'

/**
 * Model configuration parameters
 */
export interface ModelConfig {
  /** Model label */
  label?: string;
  /** Default model configuration ID */
  default_model_config_id?: string;
  /** Temperature for response randomness (0-2) */
  temperature: number;
  /** Maximum tokens in response */
  max_tokens: number;
  /** Top-p sampling parameter */
  top_p: number;
  /** Frequency penalty */
  frequency_penalty: number;
  /** Presence penalty */
  presence_penalty: number;
  /** Number of completions to generate */
  n: number;
  /** Stop sequences */
  stop?: string;
}

/**
 * Memory configuration
 */
export interface MemoryConfig {
  /** Whether memory is enabled */
  enabled: boolean;
  /** Memory content */
  memory_content?: string;
  /** Maximum history length */
  max_history?: number | string;
}

/**
 * Application configuration
 */
export interface Config extends MultiAgentConfig {
  /** Configuration ID */
  id: string;
  /** Application ID */
  app_id: string;
  /** System prompt */
  system_prompt: string;
  /** Default model configuration ID */
  default_model_config_id?: string;
  /** Model parameters */
  model_parameters: ModelConfig;
  /** Knowledge retrieval configuration */
  knowledge_retrieval: KnowledgeConfig | null;
  /** Memory configuration */
  memory?: MemoryConfig;
  /** Variables list */
  variables: Variable[];
  /** Tools list */
  tools: ToolOption[];
  /** Whether configuration is active */
  is_active: boolean;
  /** Creation timestamp */
  created_at: number;
  /** Last update timestamp */
  updated_at: number;
  skills?: SkillConfigForm | null;
}

/**
 * Multi-agent configuration
 */
export interface MultiAgentConfig {
  /** Configuration ID */
  id: string;
  /** Application ID */
  app_id: string;
  /** Default model configuration ID */
  default_model_config_id?: string;
  /** Model parameters */
  model_parameters: ModelConfig;
  /** Sub-agents list */
  sub_agents?: SubAgentItem[];
  /** Routing rules */
  routing_rules: null;
  /** Orchestration mode */
  orchestration_mode: 'supervisor' | 'collaboration';
  /** Execution configuration */
  execution_config: {
    /** Sub-agent execution mode */
    sub_agent_execution_mode: 'sequential' | 'parallel';
  };
  /** Aggregation strategy */
  aggregation_strategy: 'merge' | 'vote' | 'priority'
}

/**
 * Application modal form data
 */
export interface ApplicationModalData {
  /** Application name */
  name: string;
  /** Application type */
  type: string;
  /** Application icon */
  icon: string;
}

/**
 * Agent component ref methods
 */
export interface AgentRef {
  /**
   * Save agent configuration
   * @param flag - Whether to show success message
   */
  handleSave: (flag?: boolean) => Promise<unknown>;
}

/**
 * Cluster component ref methods
 */
export interface ClusterRef {
  /**
   * Save cluster configuration
   * @param flag - Whether to show success message
   */
  handleSave: (flag?: boolean) => Promise<unknown>;
}

/**
 * Workflow component ref methods
 */
export interface WorkflowRef {
  /**
   * Save workflow configuration
   * @param flag - Whether to show success message
   */
  handleSave: (flag?: boolean) => Promise<unknown>;
  /** Run workflow */
  handleRun: () => void;
  /** Graph reference */
  graphRef: GraphRef;
  /** Add variable */
  addVariable: () => void;
}

/**
 * Application modal ref methods
 */
export interface ApplicationModalRef {
  /**
   * Open application modal
   * @param application - Optional application data for edit mode
   */
  handleOpen: (application?: Config) => void;
}

/**
 * Model configuration source type
 */
export type Source = 'chat' | 'model' | 'multi_agent'

/**
 * Model configuration modal ref methods
 */
export interface ModelConfigModalRef {
  /**
   * Open model configuration modal
   * @param source - Configuration source
   * @param model - Optional model data
   */
  handleOpen: (source: Source, model?: any) => void;
}

/**
 * Model configuration modal form data
 */
export interface ModelConfigModalData {
  /** Model identifier */
  model: string;
  /** Additional configuration fields */
  [key: string]: string;
}

/**
 * AI prompt modal ref methods
 */
export interface AiPromptModalRef {
  /** Open AI prompt modal */
  handleOpen: () => void;
}

/**
 * Chat data structure
 */
export interface ChatData {
  /** Chat label */
  label?: string;
  /** Model configuration ID */
  model_config_id?: string;
  /** Model parameters */
  model_parameters?: ModelConfig;
  /** Chat messages list */
  list?: ChatItem[];
  /** Conversation ID */
  conversation_id?: string | null;
}

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

/**
 * Copy modal ref methods
 */
export interface CopyModalRef {
  /** Open copy modal */
  handleOpen: () => void;
}

/**
 * Sub-agent item data
 */
export interface SubAgentItem {
  /** Agent ID */
  agent_id: string;
  /** Agent name */
  name: string;
  /** Agent role */
  role: string;
  /** Agent capabilities */
  capabilities: string[];
  /** Whether agent is active */
  is_active?: boolean;
}

/**
 * Sub-agent modal ref methods
 */
export interface SubAgentModalRef {
  /**
   * Open sub-agent modal
   * @param agent - Optional agent data for edit mode
   */
  handleOpen: (agent?: SubAgentItem) => void;
}

/**
 * API key modal ref methods
 */
export interface ApiKeyModalRef {
  /** Open API key modal */
  handleOpen: () => void;
}

/**
 * API key configuration modal ref methods
 */
export interface ApiKeyConfigModalRef {
  /**
   * Open API key configuration modal
   * @param apiKey - API key data
   */
  handleOpen: (apiKey: ApiKey) => void;
}

/**
 * AI prompt variable modal ref methods
 */
export interface AiPromptVariableModalRef {
  /** Open AI prompt variable modal */
  handleOpen: () => void;
}

/**
 * AI prompt form data
 */
export interface AiPromptForm {
  /** Model ID */
  model_id?: string;
  /** Message content */
  message?: string;
  /** Current prompt */
  current_prompt?: string;
  skill?: boolean;
}

/**
 * Chat variable configuration modal ref methods
 */
export interface ChatVariableConfigModalRef {
  /**
   * Open chat variable configuration modal
   * @param values - Variables list
   */
  handleOpen: (values: Variable[]) => void;
}

/**
 * Statistics item data
 */
export interface StatisticsItem {
  /** Count value */
  count: number;
  /** Date string */
  date: string;
}

/**
 * Statistics data structure
 */
export interface StatisticsData {
  /** Daily conversations statistics */
  daily_conversations: StatisticsItem[];
  /** Daily new users statistics */
  daily_new_users: StatisticsItem[];
  /** Daily API calls statistics */
  daily_api_calls: StatisticsItem[];
  /** Daily tokens usage statistics */
  daily_tokens: StatisticsItem[];
  /** Total conversations count */
  total_conversations: number;
  /** Total new users count */
  total_new_users: number;
  /** Total API calls count */
  total_api_calls: number;
  /** Total tokens used */
  total_tokens: number;
}