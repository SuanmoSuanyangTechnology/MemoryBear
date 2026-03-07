/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:06:18 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-07 17:10:59
 */
import LoopNode from './components/Nodes/LoopNode';
import NormalNode from './components/Nodes/NormalNode';
import ConditionNode from './components/Nodes/ConditionNode';
import GroupStartNode from './components/Nodes/GroupStartNode';
import AddNode from './components/Nodes/AddNode'
import type { PortMetadata, GroupMetadata } from '@antv/x6/lib/model/port';
import type { ReactShapeConfig } from '@antv/x6-react-shape';

// Import workflow icons
import startIcon from '@/assets/images/workflow/start.svg';
import endIcon from '@/assets/images/workflow/end.svg';
import llmIcon from '@/assets/images/workflow/llm.svg';
import ragIcon from '@/assets/images/workflow/rag.svg';
import parameterExtractionIcon from '@/assets/images/workflow/parameter_extraction.svg';
import conditionIcon from '@/assets/images/workflow/condition.svg';
import iterationIcon from '@/assets/images/workflow/iteration.svg';
import loopIcon from '@/assets/images/workflow/loop.svg';
import aggregatorIcon from '@/assets/images/workflow/aggregator.svg';
import httpRequestIcon from '@/assets/images/workflow/http_request.svg';
import toolsIcon from '@/assets/images/workflow/tools.svg';
import codeExecutionIcon from '@/assets/images/workflow/code_execution.svg';
import templateRenderingIcon from '@/assets/images/workflow/template_rendering.svg';
import questionClassifierIcon from '@/assets/images/workflow/question-classifier.svg'
import breakIcon from '@/assets/images/workflow/break.svg'
import assignerIcon from '@/assets/images/workflow/assigner.svg'
import memoryReadIcon from '@/assets/images/workflow/memory-read.svg'
import memoryWriteIcon from '@/assets/images/workflow/memory-write.svg'
import unknownIcon from '@/assets/images/workflow/unknown.svg'

import { memoryConfigListUrl } from '@/api/memory'
import type { NodeLibrary } from './types'

/**
 * Workflow node library configuration
 * Defines all available node types, their icons, and configuration schemas
 */
export const nodeLibrary: NodeLibrary[] = [
  {
    category: "coreNode",
    nodes: [
      { type: "start", icon: startIcon,
        config: {
          variables: {
            type: 'define',
            sys: [
              {
                name: "message",
                type: "string",
                readonly: true
              },
              {
                name: "conversation_id",
                type: "string",
                readonly: true
              },
              {
                name: "execution_id",
                type: "string",
                readonly: true
              },
              {
                name: "workspace_id",
                type: "string",
                readonly: true
              },
              {
                name: "user_id",
                type: "string",
                readonly: true
              },
              {
                name: "files",
                type: "array[file]",
                readonly: true
              },
            ],
            defaultValue: []
          }
        }
      },
      {
        type: "end", icon: endIcon,
        config: {
          output: {
            type: 'editor'
          }
        }
      },
      // { type: "answer", icon: answerIcon },
    ]
  },
  {
    category: "aiAndCognitiveProcessing",
    nodes: [
      { type: "llm", icon: llmIcon,
        config: {
          model_id: {
            type: 'define',
            params: { type: 'llm,chat' }, // llm/chat
            valueKey: 'id',
            labelKey: 'name',
          },
          temperature: {
            type: 'define',
            max: 2, 
            min: 0, 
            step: 0.1,
            defaultValue: 0.7
          },
          max_tokens: { 
            type: 'define',
            max: 32000, 
            min: 256, 
            step: 1, 
            defaultValue: 2000 
          },
          context: {
            type: 'variableList',
            placeholder: 'workflow.config.llm.contextPlaceholder'
          },
          messages: {
            type: 'define',
            defaultValue: [
              {
                role: 'SYSTEM',
                content: undefined,
                readonly: true
              },
            ],
            placeholder: 'workflow.config.llm.messagesPlaceholder'
          },
          memory: {
            type: 'memoryConfig',
            defaultValue: {
              enable: false,
              enable_window: false,
              window_size: 20
            }
          },
          vision: {
            type: 'switch'
          },
          vision_input: {
            type: 'variableList',
            onFilterVariableNames: ['sys.files']
          }
        }
      },
      { type: "knowledge-retrieval", icon: ragIcon,
        config: {
          query: {
            type: 'variableList',
          },
          knowledge_retrieval: {
            type: 'knowledge'
          }
        }
      },
      { type: "parameter-extractor", icon: parameterExtractionIcon,
        config: {
          model_id: {
            type: 'modelSelect',
            params: { type: 'llm,chat' }, // llm/chat
          },
          text: {
            type: 'variableList',
            filterLoopIterationVars: true,
            placeholder: 'workflow.config.parameter-extractor.textPlaceholder'
          },
          params: {
            type: 'paramList',
          },
          prompt: {
            type: 'messageEditor',
            isArray: false,
            titleVariant: 'borderless',
            placeholder: 'workflow.config.parameter-extractor.promptPlaceholder'
          },
        }
      }
    ]
  },
  {
    category: "cognitiveUpgrading",
    nodes: [
      { type: "memory-read", icon: memoryReadIcon,
        config: {
          message: {
            type: 'editor',
            isArray: false
          },
          config_id: {
            type: 'customSelect',
            url: memoryConfigListUrl,
            valueKey: 'config_id',
            labelKey: 'config_name'
          },
          search_switch: {
            type: 'select',
            options: [
              { value: '0', label: 'memoryConversation.deepThinking' },
              { value: '1', label: 'memoryConversation.normalReply' },
              { value: '2', label: 'memoryConversation.quickReply' },
            ],
            needTranslation: true
          }
        }
      },
      { type: "memory-write", icon: memoryWriteIcon,
        config: {
          message: {
            type: 'editor',
            isArray: false,
            hidden: true,
          },
          messages: {
            type: 'messageEditor',
            defaultValue: [],
            placeholder: 'workflow.config.llm.messagesPlaceholder',
            isArray: true
          },
          config_id: {
            type: 'customSelect',
            url: memoryConfigListUrl,
            valueKey: 'config_id',
            labelKey: 'config_name'
          }
        }
      },
    ]
  },
  {
    category: "flowControl",
    nodes: [
      { type: "if-else", icon: conditionIcon,
        config: {
          cases: {
            type: 'caseList',
            defaultValue: [
              {
                logical_operator: 'and',
                expressions: []
              }
            ]
          }
        }
      },
      { type: "question-classifier", icon: questionClassifierIcon,
        config: {
          model_id: {
            type: 'modelSelect',
            params: { type: 'llm,chat' }, // llm/chat
          },
          input_variable: {
            type: 'variableList',
          },
          categories: {
            type: 'categoryList',
            defaultValue: [
              {},
              {}
            ]
          },
          user_supplement_prompt: {
            type: 'messageEditor',
            isArray: false,
            titleVariant: 'borderless',
            placeholder: 'common.pleaseEnter'
          }
        }
      },
      { type: "iteration", icon: iterationIcon,
        config: {
          input: {
            type: 'variableList',
            filterNodeTypes: ['knowledge-retrieval', 'iteration', 'loop', 'parameter-extractor', 'code', 'CONVERSATION'],
            filterVariableNames: ['message']
          },
          parallel: {
            type: 'switch',
            defaultValue: false
          },
          parallel_count: {
            type: 'slider',
            min: 1,
            max: 10,
            step: 1,
            defaultValue: 10,
            dependsOn: 'parallel',
            dependsOnValue: true
          },
          flatten: { // Flatten output
            type: 'switch',
            defaultValue: false
          },
          output: {
            type: 'variableList',
            filterChildNodes: true
          },
          output_type: {
            type: 'define',
          }
        },
      },
      { type: "loop", icon: loopIcon,
        config: {
          cycle_vars: {
            type: 'cycleVarsList',
            defaultValue: []
          },
          condition: {
            type: 'conditionList',
            showLabel: true,
            defaultValue: {
              logical_operator: 'and',
              expressions: []
            }
          },
          max_loop: {
            type: 'slider',
            min: 1,
            max: 100,
            step: 1,
            defaultValue: 10
          },
        }
      },
      { type: "cycle-start", icon: startIcon },
      { type: "break", icon: breakIcon },
      { type: "var-aggregator", icon: aggregatorIcon,
        config: {
          group: {
            type: 'switch',
            defaultValue: false
          },
          group_variables: {
            type: 'groupVariableList',
            defaultValue: [],
          },
          group_type: {
            type: 'define',
          }
        }
      },
      { type: "assigner", icon: assignerIcon,
        config: {
          assignments: {
            type: 'assignmentList',
            filterLoopIterationVars: true
          }
        }
      },
    ]
  },
  {
    category: "externalInteraction",
    nodes: [
      { type: "http-request", icon: httpRequestIcon,
        config: {
          method: {
            type: 'select',
            options: [
              { label: 'GET', value: 'GET' },
              { label: 'POST', value: 'POST' },
              { label: 'HEAD', value: 'HEAD' },
              { label: 'PATCH', value: 'PATCH' },
              { label: 'PUT', value: 'PUT' },
              { label: 'DELETE', value: 'DELETE' },
            ],
            defaultValue: 'GET'
          },
          url: {
            type: 'messageEditor',
            isArray: false,
          },
          auth: {
            type: 'define',
            defaultValue: {
              auth_type: 'none'
            }
          },
          headers: {
            type: 'define',
            defaultValue: []
          },
          params: {
            type: 'define',
            defaultValue: []
          },
          body: {
            type: 'define',
            defaultValue: {
              'content_type': 'none'
            }
          },
          verify_ssl: {
            type: 'switch',
            defaultValue: false
          },
          timeouts: {
            type: 'define',
            defaultValue: {}
          },
          retry: {
            type: 'switch',
            defaultValue: {
              enable: false
            }
          },
          error_handle: {
            type: 'define',
            defaultValue: {
              method: 'none'
            }
          }
        }
      },
      { type: "tool", icon: toolsIcon, 
        config: {
          tool_id: {
            type: 'cascader'
          },
          tool_parameters: {
            type: 'define'
          }
        }
      },
      { type: "code", icon: codeExecutionIcon,
        config: {
          input_variables: {
            type: 'inputList',
            defaultValue: [{ name: 'arg1' }, { name: 'arg2' }]
          },
          language: {
            type: 'select',
            defaultValue: 'python3'
          },
          code: {
            type: 'messageEditor',
            isArray: false,
            language: ['python3', 'javascript'],
            titleVariant: 'borderless',
            defaultValue: `def main(arg1: str, arg2: str):
    return {
        "result": arg1 + arg2,
    }`
          },
          output_variables: {
            type: 'outputList',
            defaultValue: [{name: 'result', type: 'string'}]
          },
        }
      },
      { type: "jinja-render", icon: templateRenderingIcon,
        config: {
          mapping: {
            type: 'mappingList',
            defaultValue: [{name: 'arg1'}]
          },
          template: {
            type: 'messageEditor',
            isArray: false,
            language: 'jinja2',
            titleVariant: 'borderless',
            defaultValue: "{{arg1}}"
          },
        }
      },
    ]
  },
];
export const unknownNode = {
  type: 'unknown',
  icon: unknownIcon
}

export const nodeWidth = 240;

export const conditionNodePortItemArgsY = 60;
export const conditionNodeItemHeight = 26;
export const conditionNodeHeight = 110;
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
];

/**
 * Port configuration interface
 */
interface PortsConfig {
  /** Port group metadata */
  groups?: GroupMetadata;
  /** Port item metadata array */
  items?: PortMetadata[];
}

/**
 * Node configuration interface
 */
interface NodeConfig {
  /** Node width in pixels */
  width: number;
  /** Node height in pixels */
  height: number;
  /** Node shape type */
  shape: string;
  /** Port configuration */
  ports?: PortsConfig;
}

/** Edge color for normal state */
export const edge_color = '#D4D5D9';
/** Edge color for selected state */
export const edge_selected_color = '#171719'
export const edge_width = 2;
/** Port color */
export const port_color = '#171719'
/**
 * Unified port markup configuration
 * Defines SVG elements for port rendering
 */
export const portMarkup = [
  {
    tagName: 'circle',
    selector: 'body',
  },
  {
    tagName: 'text',
    selector: 'label',
  },
];

/**
 * Unified port attributes configuration
 * Defines visual styling for ports
 */
export const portAttrs = {
  body: {
    r: 6, 
    magnet: true, 
    stroke: port_color, 
    strokeWidth: edge_width, 
    fill: port_color,
  },
  label: {
    text: '+',
    fontSize: 12,
    fontWeight: 'bold',
    fill: '#FFFFFF',
    textAnchor: 'middle',
    textVerticalAnchor: 'middle',
    pointerEvents: 'none',
  },
}
export const portTextAttrs = { fontSize: 12, fill: '#5B6167' }
/**
 * Port position arguments
 */
export const portItemArgsY = 26;
export const portArgs = { x: nodeWidth, y: portItemArgsY }

const defaultPortGroup = {
  position: { name: 'absolute' },
  markup: portMarkup,
  attrs: portAttrs
}

/**
 * Unified port group configuration
 * Defines port positions and attributes for different sides
 */
export const defaultAbsolutePortGroups = {
  right: defaultPortGroup,
  left: defaultPortGroup,
}
/**
 * Default port items for standard nodes
 */
export const defaultPortItems = [
  { group: 'left', args: { x: 0, y: portItemArgsY }, },
  { group: 'right', args: { x: nodeWidth, y: portItemArgsY }, },
];

/**
 * Graph node library configuration
 * Maps node types to their visual and structural properties
 */
export const graphNodeLibrary: Record<string, NodeConfig> = {
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
          id: `CASE${index}`,
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
          id: `CASE${index}`,
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
  end: {
    width: nodeWidth,
    height: 76,
    shape: 'normal-node',
    ports: {
      groups: { left: defaultPortGroup},
      items: [defaultPortItems[0]],
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
      groups: { left: defaultPortGroup },
      items: [{ group: 'left', args: { x: 0, y: 18 }}],
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
      groups: { left: defaultPortGroup },
      items: [{ group: 'left', args: { x: 0, y: 14 } }],
    },
  },
  break: {
    width: nodeWidth,
    height: 76,
    shape: 'normal-node',
    ports: {
      groups: { left: defaultPortGroup },
      items: [defaultPortItems[0]],
    },
  },
}


/**
 * Output variable configuration interface
 */
export interface OutputVariable {
  /** Default output variables */
  default?: Array<{
    name: string;
    type: string;
  }>;
  /** Dynamically defined variable keys */
  define?: string[];
  /** System-level output variables */
  sys?: Array<{
    name: string;
    type: string;
  }>;
  /** Error-related output variables */
  error?: Array<{
    name: string;
    type: string;
  }>;
}

/**
 * Output variable definitions for each node type
 * Specifies what variables each node produces
 */
export const outputVariable: { [key: string]: OutputVariable } = {
  start: {
    sys: [
      { name: "message", type: "string" },
      { name: "conversation_id", type: "string" },
      { name: "execution_id", type: "string", },
      { name: "workspace_id", type: "string" },
      { name: "user_id", type: "string" },
    ],
    define: ['variables']
  },
  end: {
  },
  llm: {
    default: [
      { name: "output", type: "string" },
    ]
  },
  'knowledge-retrieval': {
    default: [
      { name: "output", type: "array[object]" },
    ]
  },
  'parameter-extractor': {
    default: [
      { name: "__is_success", type: "number" },
      { name: "__reason", type: "string" },
    ],
    define: ['params']
  },
  'memory-read': {
    default: [
      { name: "answer", type: "string" },
      { name: "intermediate_outputs", type: "array[object]" },
    ],
  },
  'memory-write': {

  },
  'if-else': {

  },
  'question-classifier': {
    default: [
      { name: "class_name", type: "string" },
      // { name: "output", type: "string" },
    ],
  },
  'iteration': {
    default: [
      // { name: "item", type: "string" }, // 仅内部使用
      { name: "output", type: "array[string]" },
    ],
  },
  'loop': {
    define: ['cycle_vars']
  },
  'cycle-start': {

  },
  'break': {

  },
  'var-aggregator': {
    // default: [
    //   { name: "output", type: "string" },
    // ],
    define: ['group_variables']
  },
  'assigner': {

  },
  'http-request': {
    default: [
      { name: "body", type: "string" },
      { name: "status_code", type: "number" },
    ],
  },
  'tool': {
    default: [
      { name: "data", type: "string" },
    ],
  },
  'jinja-render': {
    default: [
      { name: "output", type: "string" },
    ],
  },
}

/**
 * Default edge attributes configuration
 * Defines visual styling for edges/connections
 */
export const edgeAttrs = {
  attrs: {
    line: {
      stroke: edge_color,
      strokeWidth: edge_width,
      targetMarker: {
        name: 'block',
        width: 4,
        height: 4,
      },
    },
  },
}