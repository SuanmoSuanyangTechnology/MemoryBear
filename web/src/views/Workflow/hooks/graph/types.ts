import type { Graph, Node } from '@antv/x6';
import type { RefObject, Dispatch, SetStateAction, MutableRefObject, DragEvent } from 'react';

import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import type { ChatVariable, EnvVariable, HistoryRecord, WorkflowConfig } from '../../types';
import type { Application } from '@/views/ApplicationManagement/types'
import type { Memory } from '@/views/MemoryManagement/types'

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
