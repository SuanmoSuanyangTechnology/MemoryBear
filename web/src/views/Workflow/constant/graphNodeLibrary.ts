import { nodeWidth, conditionNodeHeight, conditionNodePortItemArgsY } from './layout';
import type { NodeConfig } from './ports';
import {
  defaultPortGroup,
  leftPortGroup,
  defaultAbsolutePortGroups,
  defaultPortItems,
  portArgs,
  portItemArgsY,
} from './ports';

/**
 * Graph node library configuration
 * Maps node types to their visual and structural properties
 */
export const graphNodeLibrary: Record<string, NodeConfig> = {
  'trigger': {
    width: nodeWidth,
    height: 76,
    shape: 'normal-node',
    ports: {
      groups: { right: defaultPortGroup },
      items: [defaultPortItems[1]],
    },
  },
  iteration: {
    width: nodeWidth,
    height: 140,
    shape: 'iteration-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: defaultPortItems,
    },
  },
  loop: {
    width: nodeWidth,
    height: 140,
    shape: 'loop-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: defaultPortItems,
    },
  },
  'if-else': {
    width: nodeWidth,
    height: conditionNodeHeight,
    shape: 'condition-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: [
        defaultPortItems[0],
        ...(['IF', 'ELSE'].map((_, index) => ({
          group: 'right',
          id: `CASE${index + 1}`,
          args: {
            ...portArgs,
            y: portItemArgsY * index + conditionNodePortItemArgsY,
          },
        }))),
      ],
    },
  },
  'question-classifier': {
    width: nodeWidth,
    height: conditionNodeHeight,
    shape: 'condition-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: [
        defaultPortItems[0],
        ...(['分类1', '分类2'].map((_text, index) => ({
          group: 'right',
          id: `CASE${index + 1}`,
          args: {
            ...portArgs,
            y: portItemArgsY * index + conditionNodePortItemArgsY,
          },
        }))),
      ],
    },
  },
  'human-intervention': {
    width: nodeWidth,
    height: conditionNodeHeight,
    shape: 'condition-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: [
        defaultPortItems[0],
        ...(['TIMEOUT'].map((text, index) => ({
          group: 'right',
          id: text,
          args: {
            ...portArgs,
            y: portItemArgsY * index + conditionNodePortItemArgsY,
          },
        }))),
      ],
    },
  },
  start: {
    width: nodeWidth,
    height: 76,
    shape: 'normal-node',
    ports: {
      groups: { right: defaultPortGroup},
      items: [defaultPortItems[1]],
    },
  },
  'cycle-start': {
    width: 36,
    height: 36,
    shape: 'cycle-start',
    ports: {
      groups: { right: defaultPortGroup },
      items: [{ group: 'right', args: { x: 36, y: 18 } }],
    },
  },
  'add-node': {
    width: 100,
    height: 28,
    shape: 'add-node',
    ports: {
      groups: { left: leftPortGroup },
      items: [{ group: 'left', args: { x: 0, y: 18 }}],
    },
  },
  'memory-read': {
    width: nodeWidth,
    height: 84,
    shape: 'normal-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: defaultPortItems,
    },
  },
  'memory-write': {
    width: nodeWidth,
    height: 84,
    shape: 'normal-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: defaultPortItems,
    },
  },
  default: {
    width: nodeWidth,
    height: 76,
    shape: 'normal-node',
    ports: {
      groups: defaultAbsolutePortGroups,
      items: defaultPortItems,
    },
  },
  cycleStart: {
    width: 36,
    height: 36,
    shape: 'cycle-start',
    ports: {
      groups: { right: defaultPortGroup },
      items: [{ group: 'right', args: { x: 36, y: 18 }}],
    },
  },
  addStart: {
    width: 100,
    height: 28,
    shape: 'add-node',
    ports: {
      groups: { left: leftPortGroup },
      items: [{ group: 'left', args: { x: 0, y: 14 } }],
    },
  },
  break: {
    width: nodeWidth,
    height: 76,
    shape: 'normal-node',
    ports: {
      groups: { left: leftPortGroup },
      items: [defaultPortItems[0]],
    },
  },
  notes: {
    width: nodeWidth,
    height: 120,
    shape: 'notes-node',
  },
  output: {
    width: nodeWidth,
    height: 76,
    shape: 'normal-node',
    ports: {
      groups: { left: leftPortGroup },
      items: [defaultPortItems[0]],
    },
  }
}
