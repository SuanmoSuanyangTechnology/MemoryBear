/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:06:18 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-05 19:56:42
 */
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import type { GroupMetadata, PortMetadata } from '@antv/x6/lib/model/port';

import AddNode from './components/Nodes/AddNode';
import ConditionNode from './components/Nodes/ConditionNode';
import GroupStartNode from './components/Nodes/GroupStartNode';
import LoopNode from './components/Nodes/LoopNode';
import NormalNode from './components/Nodes/NormalNode';
import NoteNode from './components/Nodes/NoteNode';

import { memoryConfigListUrl } from '@/api/memory';
import type { NodeLibrary } from './types';

// Nodes with Data Processing in Execution Results
export const hasProcessNodes = [
  'llm',
  'knowledge-retrieval',
  'parameter-extractor',
  'memory-read',
  'memory-write',
  'question-classifier',
  'if-else',
  'assigner',
  'http-request',
  'tool',
  'code',
  'document-extractor',
]
export const hasErrorHandleNodes = [
  'code',
  'http-request',
  'llm',
  'agent'
]
// support single run node
export const cannotRunNodes = [
  'end',
  'output',
]
export const scheduleNodeConfig = {
  cron: {
    type: 'define',
    // required: true,
  },
  // frequency: {
  //   type: 'define',
  //   defaultValue: 'daily'
  // },
  // minute: {
  //   type: 'define',
  //   defaultValue: 0,
  // },
  // time: {
  //   type: 'define',
  //   defaultValue: '12:00 AM',
  // },
  // week_days: {
  //   type: 'define',
  //   defaultValue: []
  // },
  // month_days: {
  //   type: 'define',
  //   defaultValue: []
  // },
}
export const webhookNodeInitConfig = {
  method: {
    type: 'define',
    defaultValue: 'POST'
  },
  route_key: {
    type: 'define',
  },
  content_type: {
    type: 'define',
    defaultValue: 'application/json',
  },
  query_params: {
    type: 'define',
    defaultValue: []
  },
  header_params: {
    type: 'define',
    defaultValue: []
  },
  req_body_params: {
    type: 'define',
    defaultValue: []
  },
  response: {
    type: 'define',
    defaultValue: {
      status_code: 200,
      body: undefined
    }
  }
}
const modelConfig: Record<string, any> = {
  model_id: {
    type: 'define',
    required: true,
    params: { type: 'llm,chat' }, // llm/chat
    valueKey: 'id',
    labelKey: 'name',
  },
  temperature: {
    type: 'define',
    defaultValue: 0.7
  },
  max_tokens: { 
    type: 'define',
    defaultValue: 8000 
  },
  json_output: {
    type: 'define',
    defaultValue: false
  },
  // Top P 采样参数
  top_p: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 0.8
    }
  },
  // 取样数量
  top_k: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 50
    }
  },
  // 随机种子
  seed: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 1234
    }
  },
  // 重复惩罚
  repetition_penalty: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 1.0
    }
  },
  // 联网搜索
  // enable_search: {
  //   type: 'define',
  //   defaultValue: false
  // },
  // 思考模式
  thinking: {
    type: 'define',
    defaultValue: {
      budget: {
          enable: false,
          value: 256
      },
      enable: false
    }
  },
  // 回复格式
  response_format: {
    type: 'define',
    options: [
      { label: 'text', value: 'text' },
      { label: 'json_object', value: 'json_object' },
    ],
    defaultValue: 'text',
  },
  // 额外请求头，字符串格式
  extra_headers: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: undefined
    }
  },
  // 停止序列, 输入序列并按 Tab 键
  stop: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: []
    }
  },
  // 存在惩罚
  presence_penalty: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 0
    }
  },
  // 频率惩罚
  frequency_penalty: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 0
    }
  }
}
/**
 * Workflow node library configuration
 * Defines all available node types, their icons, and configuration schemas
 */
export const nodeLibrary: NodeLibrary[] = [
  {
    category: "coreNode",
    nodes: [
      { type: "start", icon: 'rb:bg-[url("@/assets/images/workflow/start.svg")]',
        config: {
          variables: {
            type: 'define',
            defaultValue: []
          }
        }
      },
      { type: "trigger", icon: 'rb:bg-[url("@/assets/images/workflow/trigger.svg")]',
        config: {
          trigger_type: {
            type: 'define',
          },
          enabled: {
            type: 'define',
            defaultValue: true
          },
          cron: {
            type: 'define',
            // required: true,
          },
          // frequency: {
          //   type: 'define',
          //   defaultValue: 'daily'
          // },
          // minute: {
          //   type: 'define',
          //   defaultValue: 0,
          // },
          // time: {
          //   type: 'define',
          //   defaultValue: '12:00 AM',
          // },
          // week_days: {
          //   type: 'define',
          //   defaultValue: []
          // },
          // month_days: {
          //   type: 'define',
          //   defaultValue: []
          // },
          method: {
            type: 'define',
            defaultValue: 'POST'
          },
          route_key: {
            type: 'define',
          },
          content_type: {
            type: 'define',
            defaultValue: 'application/json',
          },
          query_params: {
            type: 'define',
            defaultValue: []
          },
          header_params: {
            type: 'define',
            defaultValue: []
          },
          req_body_params: {
            type: 'define',
            defaultValue: []
          },
          response: {
            type: 'define',
            defaultValue: {
              status_code: 200,
              body: undefined
            }
          }
        }
      },
      { type: "end", icon: 'rb:bg-[url("@/assets/images/workflow/end.svg")]',
        config: {
          output: {
            type: 'editor',
            required: true,
          }
        }
      },
      { type: "output", icon: 'rb:bg-[url("@/assets/images/workflow/output.svg")]',
        config: {
          outputs: {
            type: 'mappingList',
            required: true,
            isNeedType: true
          }
        }
      },
      // { type: "answer", icon: answerIcon },
    ]
  },
  {
    category: "aiAndCognitiveProcessing",
    nodes: [
      { type: "llm", icon: 'rb:bg-[url("@/assets/images/workflow/llm.svg")]',
        config: {
          ...modelConfig,

          context: {
            type: 'variableList',
            placeholder: 'workflow.config.llm.contextPlaceholder'
          },
          messages: {
            type: 'define',
            required: true,
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
            onFilterVariableType: ['array[file]', 'file']
          },
          // 启用推理标签分离
          enable_reasoning_content_extraction: {
            type: 'switch',
            tip: 'workflow.config.llm.enable_reasoning_content_extraction_tip',
            defaultValue: false
          },
          // 失败重试
          retry: {
            type: 'retry',
            defaultValue: {
              enable: false,
              max_attempts: 3,
              retry_interval: 100,
            }
          },
          error_handle: {
            type: 'errorHandle',
            defaultValue: {
              method: 'none', // 'none' | 'branch' | 'default'
            },
          }
        }
      },
      { type: "agent", icon: 'rb:bg-[url("@/assets/images/workflow/agent.svg")]',
        config: {
          strategy: {
            type: 'select',
            options: [
              { label: 'ReAct', value: 'react' },
              { label: 'FunctionCalling', value: 'function_calling' },
            ],
            defaultValue: 'react',
            required: true,
          },
          model: {
            type: 'define',
            defaultValue: Object.entries(modelConfig).reduce((acc, [key, value]) => {
              acc[key] = value.defaultValue;
              return acc;
            }, {} as Record<string, any>),
            required: true,
          },
          tools: {
            type: 'toolList',
            defaultValue: [],
          },
          system_prompt: {
            type: 'messageEditor',
            isArray: false,
            titleVariant: 'borderless',
            placeholder: 'workflow.config.parameter-extractor.promptPlaceholder',
            required: true,
          },

          context: {
            type: 'variableList',
            placeholder: 'workflow.config.llm.contextPlaceholder'
          },
          message: {
            type: 'messageEditor',
            isArray: false,
            titleVariant: 'borderless',
            placeholder: 'workflow.config.parameter-extractor.promptPlaceholder',
            required: true,
          },
          max_iterations: {
            type: 'slider',
            min: 1,
            max: 10,
            step: 1,
            defaultValue: 10,
          },
          memory: {
            type: 'memoryConfig',
            needMsg: false,
            defaultValue: {
              enable: false,
              enable_window: false,
              window_size: 20
            }
          },
          error_handle: {
            type: 'errorHandle',
            defaultValue: {
              method: 'none', // 'none' | 'branch' | 'default'
            },
          }
        }
      },
      { type: "knowledge-retrieval", icon: 'rb:bg-[url("@/assets/images/workflow/rag.svg")]',
        config: {
          query: {
            type: 'variableList',
            required: true,
          },
          knowledge_retrieval: {
            type: 'knowledge',
            required: true,
          }
        }
      },
      { type: "parameter-extractor", icon: 'rb:bg-[url("@/assets/images/workflow/parameter_extraction.svg")]',
        config: {
          model_id: {
            type: 'modelSelect',
            required: true,
            params: { type: 'llm,chat' }, // llm/chat
          },
          text: {
            type: 'variableList',
            required: true,
            filterLoopIterationVars: true,
            placeholder: 'workflow.config.parameter-extractor.textPlaceholder'
          },
          params: {
            type: 'paramList',
            required: true,
          },
          prompt: {
            type: 'messageEditor',
            isArray: false,
            titleVariant: 'borderless',
            placeholder: 'workflow.config.parameter-extractor.promptPlaceholder'
          },
          inference_mode: {
            type: 'define',
            defaultValue: 'prompt',
            options: [
              { value: 'function_calling', label: 'workflow.config.parameter-extractor.function_calling' },
              { value: 'prompt', label: 'workflow.config.parameter-extractor.promptCall' },
            ]
          }
        }
      }
    ]
  },
  {
    category: "cognitiveUpgrading",
    nodes: [
      { type: "memory-read", icon: 'rb:bg-[url("@/assets/images/workflow/memory-read.svg")]',
        config: {
          message: {
            type: 'editor',
            required: true,
            isArray: false
          },
          config_id: {
            type: 'customSelect',
            required: true,
            url: memoryConfigListUrl,
            valueKey: 'config_id',
            labelKey: 'config_name'
          },
          search_switch: {
            type: 'select',
            required: true,
            options: [
              { value: '0', label: 'memoryConversation.deepThinking' },
              { value: '1', label: 'memoryConversation.normalReply' },
              { value: '2', label: 'memoryConversation.quickReply' },
              { value: '3', label: 'memoryConversation.conv' },
              { value: '4', label: 'memoryConversation.metadata' },
            ],
            needTranslation: true
          }
        }
      },
      { type: "memory-write", icon: 'rb:bg-[url("@/assets/images/workflow/memory-write.svg")]',
        config: {
          message: {
            type: 'editor',
            isArray: false,
            hidden: true,
          },
          messages: {
            type: 'messageEditor',
            required: true,
            defaultValue: [],
            placeholder: 'workflow.config.llm.messagesPlaceholder',
            isArray: true
          },
          config_id: {
            type: 'customSelect',
            required: true,
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
      { type: "if-else", icon: 'rb:bg-[url("@/assets/images/workflow/condition.svg")]',
        config: {
          cases: {
            type: 'caseList',
            required: true,
            defaultValue: [
              {
                logical_operator: 'and',
                expressions: []
              }
            ]
          }
        }
      },
      { type: "question-classifier", icon: 'rb:bg-[url("@/assets/images/workflow/question-classifier.svg")]',
        config: {
          model_id: {
            type: 'modelSelect',
            required: true,
            params: { type: 'llm,chat' }, // llm/chat
          },
          input_variable: {
            type: 'variableList',
            required: true,
          },
          vision: {
            type: 'switch'
          },
          vision_input: {
            type: 'variableList',
            onFilterVariableType: ['array[file]', 'file']
          },
          categories: {
            type: 'categoryList',
            required: true,
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
      // 人工介入
      { type: "human-intervention", icon: 'rb:bg-[url("@/assets/images/workflow/human-intervention.svg")]',
        config: {
          delivery_method: {
            type: 'define',
            defaultValue: [],
            required: true,
          },
          content: {
            type: 'messageEditor',
            isArray: false,
            titleVariant: 'borderless',
            placeholder: 'common.pleaseEnter',
          },
          actions: {
            type: 'define',
            defaultValue: [],
            required: true,
          },
          timeout: {
            type: 'timeout',
            defaultValue: {
              unit: 'days', // day, hour, minute, second
              value: 3
            }
          },
          form_fields: {
            type: 'define',
            defaultValue: []
          }
        }
      },
      { type: "iteration", icon: 'rb:bg-[url("@/assets/images/workflow/iteration.svg")]',
        config: {
          input: {
            type: 'variableList',
            required: true,
            filterNodeTypes: ['start', 'knowledge-retrieval', 'iteration', 'loop', 'parameter-extractor', 'code', 'CONVERSATION'],
            // filterVariableNames: ['message']
          },
          output_type: {
            type: 'define',
          },
          output: {
            type: 'variableList',
            required: true,
            filterChildNodes: true
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
          error_handle_mode: {
            type: 'select',
            defaultValue: 'terminated',
            needTranslation: true,
            options: [
              { label: 'workflow.config.iteration.terminated', value: 'terminated' },
              { label: 'workflow.config.iteration.continue-on-error', value: 'continue-on-error' },
              { label: 'workflow.config.iteration.remove-abnormal-output', value: 'remove-abnormal-output' },
            ],
          },
          flatten: { // Flatten output
            type: 'switch',
            defaultValue: false
          },
        },
      },
      { type: "loop", icon: 'rb:bg-[url("@/assets/images/workflow/loop.svg")]',
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
      { type: "cycle-start", icon: 'rb:bg-[url("@/assets/images/workflow/start.svg")]'},
      { type: "break", icon: 'rb:bg-[url("@/assets/images/workflow/break.svg")]'},
      { type: "var-aggregator", icon: 'rb:bg-[url("@/assets/images/workflow/aggregator.svg")]',
        config: {
          group: {
            type: 'switch',
            defaultValue: false
          },
          group_variables: {
            type: 'groupVariableList',
            required: true,
            defaultValue: [],
          },
          group_type: {
            type: 'define',
          }
        }
      },
      { type: "assigner", icon: 'rb:bg-[url("@/assets/images/workflow/assigner.svg")]',
        config: {
          assignments: {
            type: 'assignmentList',
            required: true,
            filterLoopIterationVars: true
          }
        }
      },
    ]
  },
  {
    category: "externalInteraction",
    nodes: [
      { type: "http-request", icon: 'rb:bg-[url("@/assets/images/workflow/http_request.svg")]',
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
            required: true,
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
              enable: false,
              max_attempts: 3,
              retry_interval: 1000,
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
      { type: "tool", icon: 'rb:bg-[url("@/assets/images/workflow/tools.svg")]',
        config: {
          tool_id: {
            type: 'cascader',
            required: true
          },
          tool_parameters: {
            type: 'define'
          }
        }
      },
      { type: "code", icon: 'rb:bg-[url("@/assets/images/workflow/code_execution.svg")]',
        config: {
          input_variables: {
            type: 'inputList',
            required: true,
            defaultValue: [{ name: 'arg1' }, { name: 'arg2' }]
          },
          language: {
            type: 'select',
            defaultValue: 'python3'
          },
          code: {
            type: 'messageEditor',
            required: true,
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
            required: true,
            defaultValue: [{name: 'result', type: 'string'}]
          },
          retry: {
            type: 'retry',
            defaultValue: {
              enable: false,
              max_attempts: 3,
              retry_interval: 1000,
            }
          },
          error_handle: {
            type: 'errorHandle',
            defaultValue: {
              method: 'none', // 'none' | 'branch' | 'default'
            },
          }
        }
      },
      { type: "jinja-render", icon: 'rb:bg-[url("@/assets/images/workflow/template_rendering.svg")]',
        config: {
          mapping: {
            type: 'mappingList',
            required: true,
            defaultValue: [{name: 'arg1'}]
          },
          template: {
            type: 'messageEditor',
            required: true,
            isArray: false,
            language: 'jinja2',
            titleVariant: 'borderless',
            defaultValue: "{{arg1}}"
          },
        }
      },
      { type: "document-extractor", icon: 'rb:bg-[url("@/assets/images/workflow/document-extractor.svg")]',
        config: {
          file_selector: {
            type: 'variableList',
            required: true,
            placeholder: 'common.pleaseSelect',
            onFilterVariableType: ['array[file]', 'file']
          }
        }
      },
      { type: "list-operator", icon: 'rb:bg-[url("@/assets/images/workflow/list-operator.svg")]',
        config: {
          input_list: {
            type: 'variableList',
            required: true,
          },
          filter_by: {
            type: 'define',
            defaultValue: {
              enabled: false,
              conditions: [{}]
            }
          },
          order_by: {
            type: 'define',
            defaultValue: {
              "enabled": false,
              "key": "",
              "value": "asc"
            }
          },
          limit: {
            type: 'define',
            defaultValue: {
              "enabled": false,
              "size": 1
            }
          },
          extract_by: {
            type: 'define',
            defaultValue: {
              "enabled": false,
              "serial": ""
            }
          },
        }
      },
    ]
  },
];

export const THEME_MAP: Record<string, { outer: string; title: string; bg: string; border: string }> = {
  blue: {
    outer: '#2E90FA',
    title: '#D1E9FF',
    bg: '#EFF8FF',
    border: '#84CAFF',
  },
  cyan: {
    outer: '#06AED4',
    title: '#CFF9FE',
    bg: '#ECFDFF',
    border: '#67E3F9',
  },
  green: {
    outer: '#16B364',
    title: '#D3F8DF',
    bg: '#EDFCF2',
    border: '#73E2A3',
  },
  yellow: {
    outer: '#EAAA08',
    title: '#FEF7C3',
    bg: '#FEFBE8',
    border: '#FDE272',
  },
  pink: {
    outer: '#EE46BC',
    title: '#FCE7F6',
    bg: '#FDF2FA',
    border: '#FAA7E0',
  },
  violet: {
    outer: '#875BF7',
    title: '#ECE9FE',
    bg: '#F5F3FF',
    border: '#C3B5FD',
  },
}

export const notesConfig = {
  type: "notes",
  icon: 'rb:bg-[url("@/assets/images/workflow/unknown.svg")]',
  config: {
    text: {
      type: 'define',
    },
    theme: {
      type: 'define',
      defaultValue: 'blue',
    },
    width: {
      type: 'define',
      width: 240,
    },
    height: {
      type: 'define',
      height: 120,
    },
    author: {
      type: 'define',
    },
    show_author: {
      type: 'define',
      defaultValue: true
    }
  }
}
export const unknownNode = {
  type: 'unknown',
  icon: 'rb:bg-[url("@/assets/images/workflow/unknown.svg")]'
}
export const noteNode = {
  type: 'notes',
  icon: 'rb:bg-[url("@/assets/images/workflow/unknown.svg")]'
}

export const nodeWidth = 240;

export const conditionNodePortItemArgsY = 56.5;
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
  {
    shape: 'notes-node',
    width: nodeWidth,
    height: 120,
    component: NoteNode,
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
  markup: [
    { tagName: 'rect', selector: 'body' },
    { tagName: 'circle', selector: 'hoverBody' },
    { tagName: 'text', selector: 'label' },
  ],
  attrs: {
    body: {
      width: 1,
      height: 8,
      x: 0.75,
      magnet: true,
      stroke: port_color,
      strokeWidth: edge_width,
      fill: port_color,
    },
    hoverBody: {
      r: 6,
      cy: 2,
      magnet: true,
      stroke: port_color,
      strokeWidth: edge_width,
      fill: port_color,
      opacity: 1,
    },
    label: {
      text: '+',
      fontSize: 12,
      fontWeight: 'bold',
      fill: '#FFFFFF',
      textAnchor: 'middle',
      textVerticalAnchor: 'middle',
      pointerEvents: 'none',
      y: '0.15em',
      opacity: 1,
    },
  },
}

const leftPortGroup = {
  position: { name: 'absolute' },
  markup: [{ tagName: 'rect', selector: 'body' }],
  attrs: {
    body: {
      width: 1,
      height: 8,
      x: -1.75,
      y: -4,
      magnet: true,
      stroke: port_color,
      strokeWidth: edge_width,
      fill: port_color,
    },
  },
}

/**
 * Unified port group configuration
 * Defines port positions and attributes for different sides
 */
export const defaultAbsolutePortGroups = {
  right: defaultPortGroup,
  left: leftPortGroup,
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
  /** Error-related output variables */
  error?: Array<{
    name: string;
    type: string;
  }>;
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
      targetMarker: null,
      sourceMarker: null,
    },
  },
}

/**
 * Edge hover tool: circular "+" button shown at midpoint on hover
 */
export const edgeHoverTool = {
  name: 'button',
  args: {
    markup: [
      {
        tagName: 'circle',
        selector: 'button',
        attrs: {
          r: 6,
          stroke: port_color,
          strokeWidth: edge_width,
          fill: port_color,
          cursor: 'pointer',
        },
      },
      {
        tagName: 'text',
        textContent: '+',
        selector: 'icon',
        attrs: {
          fontSize: 12,
          fontWeight: 'bold',
          fill: '#FFFFFF',
          textAnchor: 'middle',
          textVerticalAnchor: 'middle',
          pointerEvents: 'none',
          y: '0.3em',
        },
      },
    ],
    distance: 0.5,
    offset: { x: 0, y: 0 },
    onClick({ e, cell: edge }: any) {
      e.stopPropagation();
      const graph = edge.model?.graph;
      if (!graph) return;
      const sourceCell = graph.getCellById(edge.getSourceCellId());
      const targetCell = graph.getCellById(edge.getTargetCellId());
      const sourcePort = edge.getSourcePortId();
      const targetPort = edge.getTargetPortId();
      if (!sourceCell || !targetCell) return;
      const rect = (e.target as HTMLElement).getBoundingClientRect();
      const tempDiv = document.createElement('div');
      tempDiv.style.position = 'fixed';
      tempDiv.style.left = rect.left + 'px';
      tempDiv.style.top = rect.top + 'px';
      tempDiv.style.width = '1px';
      tempDiv.style.height = '1px';
      tempDiv.style.zIndex = '9999';
      document.body.appendChild(tempDiv);
      window.dispatchEvent(new CustomEvent('port:click', {
        detail: {
          node: sourceCell,
          port: sourcePort,
          element: tempDiv,
          rect,
          edgeInsertion: { edge, sourceCell, targetCell, sourcePort, targetPort }
        }
      }));
    },
  },
}