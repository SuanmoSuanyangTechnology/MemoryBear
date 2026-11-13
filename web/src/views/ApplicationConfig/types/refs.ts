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
  release_id?: string | null;
  resource_center: {
    enabled: boolean;
    allow_editable?: boolean;
    tags?: string[];
  };
  community_market: {
    enabled: boolean;
    description?: string;
  };
}

export interface EmbedWebsiteModalRef {
  handleOpen: () => void;
}

export interface SharePreviewRelease {
  id: string;
  version: number;
  version_name: string;
  published_at: number;
  publisher_name: string;
  name: string;
  icon: string | null;
}

export interface SharePreviewResourceCenter {
  shared: boolean;
  allow_editable: boolean;
  asset_id: string | null;
  pinned_release_id: string | null;
  pinned_version: number | null;
  status: 'published' | 'unpublished';
}

export interface SharePreviewCommunityMarket {
  submitted: boolean;
  sync_status: 'pending_sync' | 'synced' | 'sync_failed' | null;
  publication_status: string | null;
  market_resource_id: string | null;
  can_submit: boolean;
  block_reason: string | null;
}

export interface SharePreviewBuiltinTool {
  name: string;
  tool_class: string | null;
  source: 'self' | 'supervisor' | 'workflow_node' | 'sub_agent';
  source_name: string | null;
}

export interface SharePreviewCarriedDependency {
  kind: 'mcp_tool' | 'custom_tool' | 'workflow_tool' | 'skill' | 'plugin';
  name: string;
  disposition: 'bundled' | 'placeholder' | 'dropped';
  methods?: string[];
}

export interface SharePreviewBinding {
  kind: 'knowledge_base';
  name: string | null;
}

export interface SharePreviewClusterAgent {
  agent_id: string;
  name: string;
  release_id: string;
  version_name: string;
}

export interface SharePreviewBlocker {
  scope: 'resource_center' | 'community_market';
  reason: string;
  message: string;
}

export interface SharePreviewData {
  app_id: string;
  app_type: string;
  release: SharePreviewRelease;
  resource_center: SharePreviewResourceCenter;
  community_market: SharePreviewCommunityMarket;
  builtin_tools: SharePreviewBuiltinTool[];
  carried_dependencies: SharePreviewCarriedDependency[];
  bindings: SharePreviewBinding[];
  cluster_agents: SharePreviewClusterAgent[];
  blockers: SharePreviewBlocker[];
  can_submit_market: boolean;
}
