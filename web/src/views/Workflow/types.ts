
import { Graph } from '@antv/x6';
import type { KnowledgeConfig } from './components/Properties/Knowledge/types'
import type { Variable } from './components/Properties/VariableList/types'
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
  [key: string]: unknown;
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
  execution_config: {
    max_execution_time: number;
    max_iterations: number;
  }
  triggers: any[];
  is_active: boolean;
  created_at: number;
  updated_at: number;
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
  defaultValue: string;
}
export interface AddChatVariableRef {
  handleOpen: (value?: ChatVariable) => void;
}