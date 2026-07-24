/**
 * Component refs, modal refs and modal form data types.
 */
import type { Node } from '@antv/x6';
import type { ChatVariable, GraphRef, WorkflowConfig, EnvVariable } from '@/views/Workflow/types';
import type { ApiKey } from '@/views/ApiKeyManagement/types'
import type { Variable } from '../components/VariableList/types'
import type { Config } from './config'
import type { FeaturesConfigForm } from './features'

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
  features: Config['features'];
  handleSaveFeaturesConfig?: (value: FeaturesConfigForm) => void;
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
  features: Config['features'];
  handleSaveFeaturesConfig?: (value: FeaturesConfigForm) => void;
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
  chatVariables: ChatVariable[];
  envVariables: EnvVariable[];
  addEnvVariable: () => void;
  config: WorkflowConfig | null;
  features: WorkflowConfig['features'];
  handleFeaturesConfig?: () => void;
  handleSaveFeaturesConfig?: (value: FeaturesConfigForm) => void;
  nodeClick: ({ node }: { node: Node }) => void;
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
 * AI prompt modal ref methods
 */
export interface AiPromptModalRef {
  /** Open AI prompt modal */
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
 * App sharing modal ref methods
 */
export interface AppSharingModalRef {
  handleOpen: () => void;
}

export interface AppSharingForm {
  target_workspace_ids: string[];
  permission: 'readonly' | 'editable'
}

export interface EmbedWebsiteModalRef {
  handleOpen: () => void;
}
