/**
 * Core application / model / multi-agent configuration data models.
 */
import type { KnowledgeConfig } from '@/components/Knowledge/types'
import type { Variable } from '../components/VariableList/types'
import type { ToolOption } from '../components/ToolList/types'
import type { ChatItem } from '@/components/Chat/types'
import type { SkillConfigForm } from '../components/Skill/types'
import type { Capability } from '@/views/ModelManagement/types'
import type { FeaturesConfigForm } from './features'

/**
 * Model configuration parameters
 */
export interface ModelConfig {
  /** Model label */
  label?: string;
  /** Default model configuration ID */
  default_model_config_id?: string;
  capability?: Capability[];
  /** Temperature for response randomness (0-2) */
  temperature?: number;
  /** Maximum tokens in response */
  max_tokens?: number;
  /** Top-p sampling parameter */
  top_p?: number;
  /** Frequency penalty */
  frequency_penalty?: number;
  /** Presence penalty */
  presence_penalty?: number;
  /** Number of completions to generate */
  n?: number;
  /** Stop sequences */
  stop?: string;
  deep_thinking?: boolean;
  thinking_budget_tokens?: number;
  json_output?: boolean;
}

/**
 * Memory configuration
 */
export interface MemoryConfig {
  /** Whether memory is enabled */
  enabled: boolean;
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
  capability?: Capability[];
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

  features?: FeaturesConfigForm;
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
 * Chat data structure
 */
export interface ChatData {
  /** Chat label */
  label?: string;
  /** Model configuration ID */
  model_config_id?: string;
  /** Model parameters */
  model_parameters?: ModelConfig;
  /** Chat messages list (supports regenerate version arrays) */
  list?: Array<ChatItem | ChatItem[]>;
  /** Conversation ID */
  conversation_id?: string | null;
}
