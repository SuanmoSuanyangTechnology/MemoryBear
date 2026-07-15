import type { NodeLibrary } from '../types';

/* 工作流节点库 · 第 2 部分（拆分自 constant.ts） */
export const nodeLibraryPart2: NodeLibrary[] = [
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
