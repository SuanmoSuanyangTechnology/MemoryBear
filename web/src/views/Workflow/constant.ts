import LoopNode from './components/Nodes/LoopNode';
import NormalNode from './components/Nodes/NormalNode';
import ConditionNode from './components/Nodes/ConditionNode';
import GroupStartNode from './components/Nodes/GroupStartNode';
import AddNode from './components/Nodes/AddNode'
import type { PortMetadata, GroupMetadata } from '@antv/x6/lib/model/port';
import type { ReactShapeConfig } from '@antv/x6-react-shape';

// Import workflow icons
import startIcon from '@/assets/images/workflow/start.png';
import endIcon from '@/assets/images/workflow/end.png';
import answerIcon from '@/assets/images/workflow/answer.png';
import llmIcon from '@/assets/images/workflow/llm.png';
import modelSelectionIcon from '@/assets/images/workflow/model_selection.png';
import modelVotingIcon from '@/assets/images/workflow/model_voting.png';
import ragIcon from '@/assets/images/workflow/rag.png';
import classificationIcon from '@/assets/images/workflow/classification.png';
import parameterExtractionIcon from '@/assets/images/workflow/parameter_extraction.png';
import taskPlanningIcon from '@/assets/images/workflow/task_planning.png';
import reasoningControlIcon from '@/assets/images/workflow/reasoning_control.png';
import selfReflectionIcon from '@/assets/images/workflow/self_reflection.png';
import memoryEnhancementIcon from '@/assets/images/workflow/memory_enhancement.png';
import agentSchedulingIcon from '@/assets/images/workflow/agent_scheduling.png';
import agentCollaborationIcon from '@/assets/images/workflow/agent_collaboration.png';
import agentArbitrationIcon from '@/assets/images/workflow/agent_arbitration.png';
import conditionIcon from '@/assets/images/workflow/condition.png';
import iterationIcon from '@/assets/images/workflow/iteration.png';
import loopIcon from '@/assets/images/workflow/loop.png';
import parallelIcon from '@/assets/images/workflow/parallel.png';
import aggregatorIcon from '@/assets/images/workflow/aggregator.png';
import httpRequestIcon from '@/assets/images/workflow/http_request.png';
import toolsIcon from '@/assets/images/workflow/tools.png';
import codeExecutionIcon from '@/assets/images/workflow/code_execution.png';
import templateRenderingIcon from '@/assets/images/workflow/template_rendering.png';
import sensitiveDetectionIcon from '@/assets/images/workflow/sensitive_detection.png';
import outputAuditIcon from '@/assets/images/workflow/output_audit.png';
import selfOptimizationIcon from '@/assets/images/workflow/self_optimization.png';
import processEvolutionIcon from '@/assets/images/workflow/process_evolution.png';
import questionClassifierIcon from '@/assets/images/workflow/question-classifier.png'
import breakIcon from '@/assets/images/workflow/break.png'
import assignerIcon from '@/assets/images/workflow/assigner.png'
import memoryReadIcon from '@/assets/images/workflow/memory-read.png'
import memoryWriteIcon from '@/assets/images/workflow/memory-write.png'

import { memoryConfigListUrl } from '@/api/memory'

import { getModelListUrl } from '@/api/models'
import type { NodeLibrary } from './types'

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
            ],
            defaultValue: []
          }
        }
      },
      {
        type: "end", icon: endIcon,
        config: {
          output: {
            type: 'define'
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
            type: 'customSelect',
            url: getModelListUrl,
            params: { type: 'llm,chat' }, // llm/chat
            valueKey: 'id',
            labelKey: 'name',
          },
          temperature: {
            type: 'slider',
            max: 2, 
            min: 0, 
            step: 0.1,
            defaultValue: 0.7
          },
          max_tokens: { 
            type: 'slider', 
            max: 32000, 
            min: 256, 
            step: 1, 
            defaultValue: 2000 
          },
          context: {
            type: 'variableList',
          },
          messages: {
            type: 'define',
            defaultValue: [
              {
                role: 'SYSTEM',
                content: undefined,
                readonly: true
              },
            ]
          }
        }
      },
      // { type: "model_selection", icon: modelSelectionIcon },
      // { type: "model_voting", icon: modelVotingIcon },
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
      // { type: "classification", icon: classificationIcon },
      { type: "parameter-extractor", icon: parameterExtractionIcon,
        config: {
          model_id: {
            type: 'customSelect',
            url: getModelListUrl,
            params: { type: 'llm,chat' }, // llm/chat
            valueKey: 'id',
            labelKey: 'name',
          },
          text: {
            type: 'variableList',
            filterLoopIterationVars: true
          },
          params: {
            type: 'paramList',
          },
          prompt: {
            type: 'messageEditor',
            isArray: false,
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
            type: 'messageEditor',
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
            type: 'messageEditor',
            isArray: false
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
  // {
  //   category: "agentCollaborationNode",
  //   nodes: [
  //     { type: "agent_scheduling", icon: agentSchedulingIcon },
  //     { type: "agent_collaboration", icon: agentCollaborationIcon },
  //     { type: "agent_arbitration", icon: agentArbitrationIcon }
  //   ]
  // },
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
            type: 'customSelect',
            url: getModelListUrl,
            params: { type: 'llm,chat' }, // llm/chat
            valueKey: 'id',
            labelKey: 'name',
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
            isArray: false
          }
        }
      },
      { type: "iteration", icon: iterationIcon,
        config: {
          input: {
            type: 'variableList',
            filterNodeTypes: ['knowledge-retrieval'],
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
          flatten: { // 扁平化输出
            type: 'switch',
            defaultValue: false
          },
          output: {
            type: 'variableList',
            filterChildNodes: true
          }
        },
      },
      { type: "loop", icon: loopIcon,
        config: {
          cycle_vars: {
            type: 'cycleVarsList',
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
      { type: "cycle-start", icon: loopIcon },
      { type: "break", icon: breakIcon },
      // { type: "parallel", icon: parallelIcon },
      { type: "var-aggregator", icon: aggregatorIcon,
        config: {
          group: {
            type: 'switch',
            defaultValue: false
          },
          group_variables: {
            type: 'groupVariableList',
            defaultValue: [],
          }
        }
      },
      {
        type: "assigner", icon: assignerIcon,
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
            defaultValue: false
          },
          error_handle: {
            type: 'define',
            defaultValue: {
              method: 'default'
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
      // { type: "code_execution", icon: codeExecutionIcon },
      { type: "jinja-render", icon: templateRenderingIcon,
        config: {
          mapping: {
            type: 'mappingList',
            defaultValue: []
          },
          template: {
            type: 'messageEditor',
            isArray: false,
          },
        }
      }
    ]
  },
  // {
  //   category: "safetyAndCompliance",
  //   nodes: [
  //     { type: "sensitive_detection", icon: sensitiveDetectionIcon },
  //     { type: "output_audit", icon: outputAuditIcon }
  //   ]
  // },
  // {
  //   category: "evolutionAndGovernance",
  //   nodes: [
  //     { type: "self_optimization", icon: selfOptimizationIcon },
  //     { type: "process_evolution", icon: processEvolutionIcon }
  //   ]
  // },
];

// 节点注册库
export const nodeRegisterLibrary: ReactShapeConfig[] = [
  {
    shape: 'loop-node',
    width: 240,
    height: 120,
    component: LoopNode,
  },
  {
    shape: 'iteration-node',
    width: 240,
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
    width: 240,
    height: 88,
    component: ConditionNode,
  },
  {
    shape: 'cycle-start',
    width: 44,
    height: 44,
    component: GroupStartNode,
  },
  {
    shape: 'add-node',
    width: 88,
    height: 44,
    component: AddNode,
  },
];

interface PortsConfig {
  groups?: GroupMetadata;
  items?: PortMetadata[];
}

interface NodeConfig {
  width: number;
  height: number;
  shape: string;
  ports?: PortsConfig;
}

const portAttrs = {
  circle: {
    r: 4, magnet: true, stroke: '#155EEF', strokeWidth: 2, fill: '#155EEF', position: { top: 22 }
  },
}
const defaultPortGroups = {
  // top: { position: 'top', attrs: portAttrs },
  right: { position: 'right', attrs: portAttrs },
  // bottom: { position: 'bottom', attrs: portAttrs },
  left: { position: 'left', attrs: portAttrs },
}
const defaultPortItems = [
  // { group: 'top' },
  { group: 'right' },
  // { group: 'bottom' },
  { group: 'left' }
];
export const graphNodeLibrary: Record<string, NodeConfig> = {
  iteration: {
    width: 240,
    height: 120,
    shape: 'iteration-node',
    ports: {
      groups: defaultPortGroups,
      items: defaultPortItems,
    },
  },
  loop: {
    width: 240,
    height: 120,
    shape: 'loop-node',
    ports: {
      groups: defaultPortGroups,
      items: defaultPortItems,
    },
  },
  'if-else': {
    width: 240,
    height: 88,
    shape: 'condition-node',
    ports: {
      groups: defaultPortGroups,
      items: [
        { group: 'left' },
        { group: 'right', id: 'CASE1', args: { dy: 24 }, attrs: { text: { text: 'IF', fontSize: 12, color: '#5B6167' }} },
        { group: 'right', id: 'CASE2', attrs: { text: { text: 'ELSE', fontSize: 12, color: '#5B6167' }} }
      ],
    },
  },
  'question-classifier': {
    width: 240,
    height: 88,
    shape: 'condition-node',
    ports: {
      groups: defaultPortGroups,
      items: [
        { group: 'left' },
        { group: 'right', id: 'CASE1', args: { dy: 24 }, attrs: { text: { text: '分类1', fontSize: 12, color: '#5B6167' } } },
        { group: 'right', id: 'CASE2', attrs: { text: { text: '分类2', fontSize: 12, color: '#5B6167' } } }
      ],
    },
  },
  start: {
    width: 240,
    height: 64,
    shape: 'normal-node',
    ports: {
      groups: {right: { position: 'right', attrs: portAttrs }},
      items: [{ group: 'right' }],
    },
  },
  end: {
    width: 240,
    height: 64,
    shape: 'normal-node',
    ports: {
      groups: {left: { position: 'left', attrs: portAttrs }},
      items: [{ group: 'left' }],
    },
  },
  'cycle-start': {
    width: 44,
    height: 44,
    shape: 'cycle-start',
    ports: {
      groups: {right: { position: 'right', attrs: portAttrs }},
      items: [{ group: 'right' }],
    },
  },
  'add-node': {
    width: 88,
    height: 44,
    shape: 'add-node',
    ports: {
      groups: {left: { position: 'left', attrs: portAttrs }},
      items: [{ group: 'left' }],
    },
  },
  default: {
    width: 240,
    height: 64,
    shape: 'normal-node',
    ports: {
      groups: defaultPortGroups,
      items: defaultPortItems,
    },
  },
  cycleStart: {
    width: 44,
    height: 44,
    shape: 'cycle-start',
    ports: {
      groups: {right: { position: 'right', attrs: portAttrs }},
      items: [{ group: 'right' }],
    },
  },
  addStart: {
    width: 88,
    height: 44,
    shape: 'add-node',
    ports: {
      groups: {left: { position: 'left', attrs: portAttrs }},
      items: [{ group: 'left' }],
    },
  }
}