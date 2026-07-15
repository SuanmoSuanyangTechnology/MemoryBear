import type { NodeLibrary } from '../types';
import { modelConfig } from './modelConfig';

/* 工作流节点库 · 第 1 部分（拆分自 constant.ts） */
export const nodeLibraryPart1: NodeLibrary[] = [
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
          },
          metadata_filter_mode: {
            type: 'metadata',
            defaultValue: 'disabled'
          },
          metadata_model: {
            type: 'define',
            defaultValue: Object.entries(modelConfig).reduce((acc, [key, value]) => {
              acc[key] = value.defaultValue;
              return acc;
            }, {} as Record<string, any>),
          },
          metadata_filters: {
            type: 'define',
            defaultValue: {
              conditions: [],
              logic: 'and'
            }
          },
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
            type: 'activeMemoryConfig',
          },
          search_switch: {
            type: 'select',
            required: true,
            options: [
              { value: '0', label: 'memoryConversation.deepThinking' },
              { value: '1', label: 'memoryConversation.normalReply' },
              { value: '2', label: 'memoryConversation.quickReply' },
              { value: '5', label: 'memoryConversation.quickReplyPlus' },
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
            type: 'activeMemoryConfig',
          },
        }
      },
    ]
  },
];
