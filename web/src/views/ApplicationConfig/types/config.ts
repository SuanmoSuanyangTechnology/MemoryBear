/**
 * Core application / model / multi-agent configuration data models.
 */
import type { KnowledgeConfig } from '@/components/Knowledge/types'
import type { Variable } from '../components/VariableList/types'
import type { ToolOption } from '../components/ToolList/types'
import type { ChatItem } from '@/components/Chat/types'
import type { SkillConfigForm } from '../components/Skill/types'
import type { Modality, ModelFeature } from '@/views/ModelManagement/types'
import type { FeaturesConfigForm } from './features'

/**
 * Model configuration parameters
 */
export interface ModelConfig {
  features?: ModelFeature[];
  /** Model label */
  label?: string;
  /** Default model configuration ID */
  default_model_config_id?: string;
  input_modalities?: Modality[];
  output_modalities?: Modality[];
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
  input_modalities?: Modality[];
  output_modalities?: Modality[];
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

export type OrchestrationMode = 'supervisor_loop' | 'supervisor' | 'collaboration';

export type AggregationStrategy = 'merge' | 'vote' | 'priority';

export type ReleasePolicy = 'current' | 'pinned';

/**
 * Multi-agent routing rule
 */
export interface RoutingRule {
  condition: string;
  target_agent_id: string;
  priority?: number;
}

/**
 * Multi-agent execution safeguards
 */
export interface ExecutionConfig {
  max_iterations: number;
  supervisor_max_tool_calls?: number;
  stream_idle_timeout?: number;
  retry_on_failure?: boolean;
  max_retries?: number;
  timeout?: number;
  parallel_limit?: number;
  result_merge_mode?: 'master' | 'smart';
  merge_max_tokens?: number;
  sub_agent_execution_mode?: 'parallel' | 'sequential';
}

/**
 * Supervisor capabilities
 */
export interface SupervisorConfig {
  system_prompt?: string | null;
  memory?: { enabled: boolean } | null;
  knowledge_retrieval?: KnowledgeConfig | null;
  tools?: ToolOption[] | null;
  skills?: SkillConfigForm | null;
  variables?: Variable[] | null;
}

/**
 * Shared writable fields for a sub-agent
 */
export interface SubAgentConfigInputBase {
  agent_id: string;
  name?: string;
  role?: string | null;
  priority?: number;
  capabilities?: string[];
}

/**
 * Sub-agent data accepted by the update API
 */
export type SubAgentConfigInput = SubAgentConfigInputBase & (
  | {
      release_policy?: 'current';
      release_id?: null;
    }
  | {
      release_policy: 'pinned';
      release_id: string;
    }
);

/**
 * Sub-agent data returned by the configuration API
 */
export interface SubAgentConfigResponse extends SubAgentConfigInputBase {
  role: string | null;
  priority: number;
  capabilities: string[];
  release_policy: ReleasePolicy;
  release_id: string | null;
  readonly current_release_id: string | null;
  readonly has_newer_release: boolean | null;
}

/**
 * Multi-agent configuration returned by the API
 */
export interface MultiAgentConfig {
  /** Configuration ID; absent when the API returns the default template */
  id?: string;
  /** Application ID */
  app_id: string;
  /** Default model configuration ID */
  default_model_config_id?: string | null;
  /** Model parameters */
  model_parameters: ModelConfig | null;
  /** Sub-agents list */
  sub_agents: SubAgentConfigResponse[];
  /** Routing rules */
  routing_rules: RoutingRule[];
  /** Orchestration mode */
  orchestration_mode: OrchestrationMode;
  /** Reserved master-agent release ID */
  master_agent_id: string | null;
  /** Master-agent name */
  master_agent_name: string | null;
  /** Execution safeguards */
  execution_config: ExecutionConfig;
  /** Supervisor capabilities; null means all defaults */
  supervisor_config: SupervisorConfig | null;
  /** Only the default template may retain this deprecated top-level key */
  /** Aggregation strategy */
  aggregation_strategy: AggregationStrategy;
  /** Whether the saved configuration is active */
  is_active?: boolean;
  /** Creation timestamp in milliseconds */
  created_at?: number;
  /** Last update timestamp in milliseconds */
  updated_at?: number;
}

/**
 * Multi-agent configuration fields accepted by the update API
 */
export interface MultiAgentConfigUpdate {
  orchestration_mode?: OrchestrationMode;
  master_agent_name?: string | null;
  default_model_config_id?: string | null;
  model_parameters?: ModelConfig | null;
  sub_agents?: SubAgentConfigInput[];
  routing_rules?: RoutingRule[];
  execution_config?: Partial<ExecutionConfig>;
  supervisor_config?: SupervisorConfig | null;
  aggregation_strategy?: AggregationStrategy;
}

/**
 * Editable sub-agent item used by the configuration UI
 */
export interface SubAgentItem extends SubAgentConfigInputBase {
  release_policy?: ReleasePolicy;
  release_id?: string | null;
  readonly current_release_id?: string | null;
  readonly has_newer_release?: boolean | null;
  /** Whether the referenced application is active */
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
  /** Whether the model is currently streaming */
  streamLoading?: boolean;
}
