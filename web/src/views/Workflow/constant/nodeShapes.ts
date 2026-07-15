import type { ReactShapeConfig } from '@antv/x6-react-shape';

import AddNode from '../components/Nodes/AddNode';
import ConditionNode from '../components/Nodes/ConditionNode';
import GroupStartNode from '../components/Nodes/GroupStartNode';
import LoopNode from '../components/Nodes/LoopNode';
import NormalNode from '../components/Nodes/NormalNode';
import NoteNode from '../components/Nodes/NoteNode';

import { nodeWidth, conditionNodeHeight } from './layout';

/**
 * Node registration library for X6 graph
 * Maps node shapes to their React components
 */
export const nodeRegisterLibrary: ReactShapeConfig[] = [
  {
    shape: 'loop-node',
    width: nodeWidth,
    height: 120,
    component: LoopNode,
  },
  {
    shape: 'iteration-node',
    width: nodeWidth,
    height: 120,
    component: LoopNode,
  },
  {
    shape: 'normal-node',
    width: 120,
    height: 40,
    component: NormalNode,
  },
  {
    shape: 'condition-node',
    width: nodeWidth,
    height: conditionNodeHeight,
    component: ConditionNode,
  },
  {
    shape: 'cycle-start',
    width: 36,
    height: 36,
    component: GroupStartNode,
  },
  {
    shape: 'add-node',
    width: 100,
    height: 28,
    component: AddNode,
  },
  {
    shape: 'notes-node',
    width: nodeWidth,
    height: 120,
    component: NoteNode,
  },
];
