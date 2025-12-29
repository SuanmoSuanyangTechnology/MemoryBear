
import { Graph } from '@antv/x6';
export interface NodeConfig {
  type: 'input' | 'textarea' | 'select' | 'inputNumber' | 'slider' | 'customSelect' | 'define' | 'knowledge' | 'variableList';
  options?: { label: string; value: string }[];

  max?: number;
  min?: number;
  step?: number;

  url?: string;
  params?: { [key: string]: unknown; }
  valueKey?: string;
  labelKey?: string;

  defaultValue?: any | StartVariableItem[];

  sys?: Array<{
    name: string;
    type: string;
    readonly: boolean;
  }>
  [key: string]: unknown;
}

export interface NodeProperties {
  type: string;
  icon: string;
  name?: string;
  id?: string;
  config?: Record<string, NodeConfig>;
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
    default: string;
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

export interface VariableEditModalRef {
  handleOpen: (values?: StartVariableItem) => void;
}
export interface StartVariableItem {
  name: string;
  type: string;
  required: boolean;
  description: string;
  max_length?: number;
  default?: string;
  readonly?: boolean;
  defaultValue?: any;
  value?: any;
}

export interface ChatRef {
  handleOpen: () => void;
}
export type GraphRef = React.MutableRefObject<Graph | undefined>
export interface VariableConfigModalRef {
  handleOpen: (values: StartVariableItem[]) => void;
}