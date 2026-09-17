import type { RefObject, Dispatch, SetStateAction, MutableRefObject, DragEvent } from 'react';
import { Graph, Node } from '@antv/x6';
import type { KnowledgeConfig } from '@/components/Knowledge/types'
import type { Variable } from './components/Properties/VariableList/types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import type { Application } from '@/views/ApplicationManagement/types'
import type { Memory } from '@/views/MemoryManagement/types'

export interface NodeConfig {
  type: 'input' | 'textarea' | 'select' | 'inputNumber' | 'slider' | 'customSelect' | 'define' | 'knowledge' | 'variableList' | string;
  placeholder?: string;
  titleVariant?: 'outlined' | 'borderless';
  options?: { label: string; value: string }[];

  max?: number;
  min?: number;
  step?: number;

  url?: string;
  params?: { [key: string]: unknown; }
  valueKey?: string;
  labelKey?: string;

  defaultValue?: any;

  sys?: Array<{
    name: string;
    type: string;
    readonly: boolean;
  }>

  knowledge_retrieval?: KnowledgeConfig;

  group_variables?: Array<{ key: string, value: string[] }>
  cycle?: string;
  cycle_vars?: Array<{ name: string; type: string; value: string; input_type: string; }>
  required?: boolean;
  tip?: string;
  [key: string]: unknown;
}

export type AgentMode = 'inline' | 'reference'

export interface AgentReference {
  app_id?: string
  release_policy: 'current' | 'pinned'
  release_id?: string
}

export interface AgentVariableMapping {
  name: string
  value: string
}

export interface NodeProperties {
  type: string;
  icon: string;
  name?: string;
  id?: string;
  config?: Record<string, NodeConfig>;
  hidden?: boolean;
  cycle?: string;
}

export interface NodeLibrary {
  category: string;
  nodes: NodeProperties[];
}


export interface NodeItem {
  id: string;
  type: string;
  name: string;
  position: {
    x: number;
    y: number;
  };
  config: {
    [key: string]: unknown;
  };

  cycle?: string;
}
export interface EdgesItem {
  source: string;
  target: string;
  label: string;
}
export interface WorkflowConfig {
  id: string;
  app_id: string;
  nodes: NodeItem[],
  edges: EdgesItem[],
  variables: Array<{
    name: string;
    type: string;
    required: boolean;
    description: string;
    default?: string;
    defaultValue: string;
  }>,
  environment_variables: EnvVariable[],
  execution_config: {
    max_execution_time: number;
    max_iterations: number;
  }
  triggers: any[];
  is_active: boolean;
  created_at: number;
  updated_at: number;

  features?: FeaturesConfigForm;
}

export interface ChatRef {
  handleOpen: () => void;
}
export type GraphRef = React.MutableRefObject<Graph | undefined>
export interface VariableConfigModalRef {
  handleOpen: (values: Variable[]) => void;
}

export interface ChatVariable {
  name: string;
  type: string;
  required: boolean;
  description: string;
  default?: string;
  defaultValue: string | any[];
}
export interface AddChatVariableRef {
  handleOpen: (value?: ChatVariable) => void;
}

export interface EnvVariable {
  name: string;
  value_type: 'secret' | 'string' | 'number';
  required: boolean;
  value: string;
  description: string;
}
export interface AddEnvVariableRef {
  handleOpen: (value?: EnvVariable) => void;
}

export type HistoryActionType = 'add' | 'remove' | 'change' | 'undo' | 'redo' | 'batch'

export interface HistoryRecord {
  type: HistoryActionType;
  timestamp: number;
  batchName?: string;
  cellIds?: string[];
}


/**
 * Props for useWorkflowGraph hook
 */
export interface UseWorkflowGraphProps {
  /** Reference to the main graph container element */
  containerRef: RefObject<HTMLDivElement>;
  /** Reference to the minimap container element */
  miniMapRef: RefObject<HTMLDivElement>;
  /** Application type */
  appType?: Application['type'];
  setRunOpen: Dispatch<SetStateAction<boolean>>;
}

/**
 * Return type for useWorkflowGraph hook
 */
export interface UseWorkflowGraphReturn {
  /** Current workflow configuration */
  config: WorkflowConfig | null;
  /** Function to update workflow configuration */
  setConfig: Dispatch<SetStateAction<WorkflowConfig | null>>;
  /** Reference to the X6 graph instance */
  graphRef: MutableRefObject<Graph | undefined>;
  /** Currently selected node */
  selectedNode: Node | null;
  /** Function to update selected node */
  setSelectedNode: Dispatch<SetStateAction<Node | null>>;
  /** Current zoom level of the graph */
  zoomLevel: number;
  /** Function to update zoom level */
  setZoomLevel: Dispatch<SetStateAction<number>>;
  /** Whether hand/pan mode is enabled */
  isHandMode: boolean;
  /** Function to toggle hand mode */
  setIsHandMode: Dispatch<SetStateAction<boolean>>;
  /** Handler for dropping nodes onto canvas */
  onDrop: (event: DragEvent) => void;
  /** Handler for clicking blank canvas area */
  blankClick: () => void;
  /** Handler for delete keyboard event */
  deleteEvent: () => boolean | void;
  /** Handler for copy keyboard event */
  copyEvent: () => boolean | void;
  /** Handler for paste keyboard event */
  parseEvent: () => boolean | void;
  /** Whether undo is available */
  canUndo: boolean;
  /** Whether redo is available */
  canRedo: boolean;
  /** Undo last action */
  undo: () => void;
  /** Redo last undone action */
  redo: () => void;
  /** Function to save workflow configuration */
  handleSave: (flag?: boolean) => Promise<unknown>;
  /** Chat variables for workflow */
  chatVariables: ChatVariable[];
  /** Function to update chat variables */
  setChatVariables: Dispatch<SetStateAction<ChatVariable[]>>;

  envVariables: EnvVariable[];
  setEnvVariables: Dispatch<SetStateAction<EnvVariable[]>>;

  handleAddNotes: () => void;
  handleSaveFeaturesConfig: (value: FeaturesConfigForm) => void;
  features?: FeaturesConfigForm;
  /** Get start node output variable list (user-defined + system variables) */
  getStartNodeVariables: () => Array<{ name: string; type: string; readonly?: boolean }>;
  nodeClick: ({ node }: { node: Node }) => void;
  /** All recorded history operations */
  historyRecords: HistoryRecord[];
  /** Clear history records */
  clearHistoryRecords: () => void;
  activeMemoryConfig?: Memory | null;
}