/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:10:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-24 17:55:08
 */
/**
 * Workflow Chat Component
 * 
 * A drawer-based chat interface for testing and debugging workflow executions.
 * Provides real-time streaming of workflow node execution status, input/output data,
 * and error messages. Supports variable configuration and file attachments.
 * 
 * Key Features:
 * - Real-time workflow execution monitoring with SSE streaming
 * - Node-level execution tracking (start, end, error states)
 * - Variable configuration for workflow inputs
 * - File upload support (images and documents)
 * - Collapsible node execution details with input/output inspection
 * - Error handling and display
 * 
 * @component
 */
import { forwardRef, useImperativeHandle, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { App, Space, Button, Flex, Dropdown, type MenuProps } from 'antd'

import ChatIcon from '@/assets/images/application/chat.png'
import RbDrawer from '@/components/RbDrawer';
import VariableConfigModal from './VariableConfigModal'
import { draftRun } from '@/api/application';
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import dayjs from 'dayjs'
import type { ChatRef, VariableConfigModalRef, GraphRef } from '../../types'
import { type SSEMessage } from '@/utils/stream'
import type { Variable } from '../Properties/VariableList/types'
import ChatInput from '@/components/Chat/ChatInput'
import UploadFiles from '@/views/Conversation/components/FileUpload'
// import AudioRecorder from '@/components/AudioRecorder'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import type { UploadFileListModalRef } from '@/views/Conversation/types'
import Runtime from './Runtime';

const Chat = forwardRef<ChatRef, { appId: string; graphRef: GraphRef }>(({ appId, graphRef }, ref) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  // State management
  const [open, setOpen] = useState(false) // Drawer visibility
  const [loading, setLoading] = useState(false) // Send button loading state
  const [chatList, setChatList] = useState<ChatItem[]>([
    {
      "role": "assistant",
      "content": "经过多次打磨，最终作品如下：\n《咏一·三题》  \n孤光未凿太初溟，  \n一粟吞天万籁宁。  \n影堕千峰青未染，  \n心空四象白犹灵。  \n非从烛焰求明性，  \n但向尘劳见本形。  \n忽有松风穿石罅，  \n泠然吹落满山星。  \n\n注：本诗严守平水韵九青部（溟、宁、灵、形、星），其中“星”属下平声九青部异读字（《广韵》息盈切，与“灵”“宁”同部），古诗常用以协律，如王维“清溪流过碧山头，空水澄鲜一色秋。隔断红尘三十里，白云红叶两悠悠”中“悠”亦借韵通协。全诗紧扣“以一为魂”之旨：首句“孤光未凿”化《庄子·应帝王》“浑沌凿七窍而死”典，反写太初本明未分之境；次句“一粟吞天”，以微纳巨，承“一芥”而力愈雄浑；颔联“青未染”“白犹灵”，双色映照，暗喻性体离垢绝染而朗然常照；颈联直破二边——不假烛焰（破外求）、不避尘劳（破厌离），显《坛经》“佛法在世间，不离世间觉”之旨；结句松风裂石、星落满山，是“一”之活泼妙用：寂而常照，照而恒寂，恰如《道德经》“天得一以清，地得一以宁”之诗性证成。 \nLLM1结果：\n《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。 ",
      "created_at": 1771925594511,
      "subContent": [
        {
          "id": "start_1767617465337_0djnmpk2y",
          "node_id": "start_1767617465337_0djnmpk2y",
          "node_name": "开始（Start）",
          "icon": "/src/assets/images/workflow/start.png",
          "content": {
            "input": {
              "execution_id": "exec_11a80fb1cde148cb",
              "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
              "message": "1",
              "conversation_vars": {}
            },
            "output": {
              "message": "1",
              "execution_id": "exec_11a80fb1cde148cb",
              "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
              "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
              "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd",
              "topic": "",
              "number": 0,
              "Boolean": false
            }
          },
          "status": "completed",
          "elapsed_time": 0
        },
        {
          "id": "llm_1767617499720_zvqwjpw3b",
          "node_id": "llm_1767617499720_zvqwjpw3b",
          "node_name": "大语言模型 (LLM)-初始创作",
          "icon": "/src/assets/images/workflow/llm.png",
          "content": {
            "input": {
              "prompt": null,
              "messages": [
                {
                  "role": "system",
                  "content": "请根据1  为主题写一首七字诗。"
                }
              ],
              "config": {
                "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                "temperature": 0.7,
                "max_tokens": 2000
              }
            },
            "output": "《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。"
          },
          "status": "completed",
          "elapsed_time": 4.518743515014648
        },
        {
          "id": "loop_1767617552451_hq3j342ha",
          "node_id": "loop_1767617552451_hq3j342ha",
          "node_name": "循环 (Loop)",
          "icon": "/src/assets/images/workflow/loop.png",
          "content": {
            "input": {
              "config": {
                "max_loop": 10,
                "condition": {
                  "expressions": [
                    {
                      "left": "{{loop_1767617552451_hq3j342ha.round}}",
                      "right": 3,
                      "operator": "eq",
                      "input_type": "Constant"
                    }
                  ],
                  "logical_operator": "and"
                },
                "cycle_vars": [
                  {
                    "name": "poem_content",
                    "type": "string",
                    "value": "{{llm_1767617499720_zvqwjpw3b.output}}",
                    "input_type": "variable"
                  },
                  {
                    "name": "round",
                    "type": "number",
                    "value": "0",
                    "input_type": "constant"
                  }
                ]
              }
            },
            "output": {
              "poem_content": "《咏一·三题》  \n孤光未凿太初溟，  \n一粟吞天万籁宁。  \n影堕千峰青未染，  \n心空四象白犹灵。  \n非从烛焰求明性，  \n但向尘劳见本形。  \n忽有松风穿石罅，  \n泠然吹落满山星。  \n\n注：本诗严守平水韵九青部（溟、宁、灵、形、星），其中“星”属下平声九青部异读字（《广韵》息盈切，与“灵”“宁”同部），古诗常用以协律，如王维“清溪流过碧山头，空水澄鲜一色秋。隔断红尘三十里，白云红叶两悠悠”中“悠”亦借韵通协。全诗紧扣“以一为魂”之旨：首句“孤光未凿”化《庄子·应帝王》“浑沌凿七窍而死”典，反写太初本明未分之境；次句“一粟吞天”，以微纳巨，承“一芥”而力愈雄浑；颔联“青未染”“白犹灵”，双色映照，暗喻性体离垢绝染而朗然常照；颈联直破二边——不假烛焰（破外求）、不避尘劳（破厌离），显《坛经》“佛法在世间，不离世间觉”之旨；结句松风裂石、星落满山，是“一”之活泼妙用：寂而常照，照而恒寂，恰如《道德经》“天得一以清，地得一以宁”之诗性证成。",
              "round": 3,
              "__child_state": [
                {
                  "messages": [],
                  "cycle_nodes": [
                    "loop_1767617552451_hq3j342ha"
                  ],
                  "looping": 1,
                  "node_outputs": {
                    "start_1767617465337_0djnmpk2y": {
                      "node_id": "start_1767617465337_0djnmpk2y",
                      "node_type": "start",
                      "node_name": "开始（Start）",
                      "status": "completed",
                      "input": {
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "message": "1",
                        "conversation_vars": {}
                      },
                      "output": {
                        "message": "1",
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                        "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd",
                        "topic": "",
                        "number": 0,
                        "Boolean": false
                      },
                      "elapsed_time": 0,
                      "token_usage": null,
                      "error": null
                    },
                    "llm_1767617499720_zvqwjpw3b": {
                      "node_id": "llm_1767617499720_zvqwjpw3b",
                      "node_type": "llm",
                      "node_name": "大语言模型 (LLM)-初始创作",
                      "status": "completed",
                      "input": {
                        "prompt": null,
                        "messages": [
                          {
                            "role": "system",
                            "content": "请根据1  为主题写一首七字诗。"
                          }
                        ],
                        "config": {
                          "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                          "temperature": 0.7,
                          "max_tokens": 2000
                        }
                      },
                      "output": "《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。",
                      "elapsed_time": 4.518743515014648,
                      "token_usage": {
                        "prompt_tokens": 25,
                        "completion_tokens": 165,
                        "total_tokens": 190
                      },
                      "error": null
                    },
                    "loop_1767617552451_hq3j342ha": {
                      "poem_content": "《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。",
                      "round": 0
                    },
                    "21046fb8-1f33-45f7-aeda-2c196471f119": {
                      "node_id": "21046fb8-1f33-45f7-aeda-2c196471f119",
                      "node_type": "cycle-start",
                      "node_name": null,
                      "status": "completed",
                      "input": {
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "message": "1",
                        "conversation_vars": {}
                      },
                      "output": {
                        "message": "1",
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                        "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd"
                      },
                      "elapsed_time": 0.0005278587341308594,
                      "token_usage": null,
                      "error": null
                    },
                    "llm_1767617560401_bsx1vhi25": {
                      "node_id": "llm_1767617560401_bsx1vhi25",
                      "node_type": "llm",
                      "node_name": "大语言模型 (LLM)-润色器",
                      "status": "completed",
                      "input": {
                        "prompt": null,
                        "messages": [
                          {
                            "role": "system",
                            "content": "请根据《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。 为主题写一首七字诗。"
                          }
                        ],
                        "config": {
                          "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                          "temperature": 0.7,
                          "max_tokens": 2000
                        }
                      },
                      "output": "《咏一·次韵》  \n千峰削玉立空青，  \n一羽浮天亦自宁。  \n万籁收声归太始，  \n孤光未堕即长明。  \n\n注：本诗承原作“以一为魂”之旨，严守平水韵九青部（青、宁、明），平仄谐律。首句“千峰削玉”反衬“一羽浮天”，以极繁托极简；次句“一羽”既承“一芥”之微，更取《庄子》“鹏徙南冥”之逸气，言至微者亦可持守本然之宁。三句“万籁收声”暗应原作“千山雪落只无声”，而升华为宇宙初开的“太始”静界；结句“孤光未堕即长明”，化用《淮南子》“日月不为明而明”与禅宗“一念不生即佛”，昭示“一”非寂灭之空，乃不假外求、本自圆成的永恒觉性——此即《道德经》“天得一以清”的诗性证悟。",
                      "elapsed_time": 6.8497374057769775,
                      "token_usage": {
                        "prompt_tokens": 188,
                        "completion_tokens": 262,
                        "total_tokens": 450
                      },
                      "error": null
                    },
                    "assigner_1768285417545_qsoqleflh": {
                      "node_id": "assigner_1768285417545_qsoqleflh",
                      "node_type": "assigner",
                      "node_name": "变量赋值",
                      "status": "completed",
                      "input": {
                        "config": {
                          "assignments": [
                            {
                              "value": "{{llm_1767617560401_bsx1vhi25.output}}",
                              "operation": "cover",
                              "variable_selector": "{{loop_1767617552451_hq3j342ha.poem_content}}"
                            },
                            {
                              "value": 1,
                              "operation": "add",
                              "variable_selector": "{{loop_1767617552451_hq3j342ha.round}}"
                            }
                          ]
                        }
                      },
                      "output": null,
                      "elapsed_time": 0.0003705024719238281,
                      "token_usage": null,
                      "error": null
                    }
                  },
                  "execution_id": "exec_11a80fb1cde148cb",
                  "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                  "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd",
                  "error": null,
                  "error_node": null,
                  "activate": {
                    "llm_1767617560401_bsx1vhi25": true,
                    "loop_1767617552451_hq3j342ha": true,
                    "start_1767617465337_0djnmpk2y": true,
                    "21046fb8-1f33-45f7-aeda-2c196471f119": true,
                    "llm_1767617499720_zvqwjpw3b": true,
                    "assigner_1768285417545_qsoqleflh": true
                  }
                },
                {
                  "messages": [],
                  "cycle_nodes": [
                    "loop_1767617552451_hq3j342ha"
                  ],
                  "looping": 1,
                  "node_outputs": {
                    "start_1767617465337_0djnmpk2y": {
                      "node_id": "start_1767617465337_0djnmpk2y",
                      "node_type": "start",
                      "node_name": "开始（Start）",
                      "status": "completed",
                      "input": {
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "message": "1",
                        "conversation_vars": {}
                      },
                      "output": {
                        "message": "1",
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                        "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd",
                        "topic": "",
                        "number": 0,
                        "Boolean": false
                      },
                      "elapsed_time": 0,
                      "token_usage": null,
                      "error": null
                    },
                    "llm_1767617499720_zvqwjpw3b": {
                      "node_id": "llm_1767617499720_zvqwjpw3b",
                      "node_type": "llm",
                      "node_name": "大语言模型 (LLM)-初始创作",
                      "status": "completed",
                      "input": {
                        "prompt": null,
                        "messages": [
                          {
                            "role": "system",
                            "content": "请根据1  为主题写一首七字诗。"
                          }
                        ],
                        "config": {
                          "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                          "temperature": 0.7,
                          "max_tokens": 2000
                        }
                      },
                      "output": "《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。",
                      "elapsed_time": 4.518743515014648,
                      "token_usage": {
                        "prompt_tokens": 25,
                        "completion_tokens": 165,
                        "total_tokens": 190
                      },
                      "error": null
                    },
                    "loop_1767617552451_hq3j342ha": {
                      "poem_content": "《咏一·次韵》  \n千峰削玉立空青，  \n一羽浮天亦自宁。  \n万籁收声归太始，  \n孤光未堕即长明。  \n\n注：本诗承原作“以一为魂”之旨，严守平水韵九青部（青、宁、明），平仄谐律。首句“千峰削玉”反衬“一羽浮天”，以极繁托极简；次句“一羽”既承“一芥”之微，更取《庄子》“鹏徙南冥”之逸气，言至微者亦可持守本然之宁。三句“万籁收声”暗应原作“千山雪落只无声”，而升华为宇宙初开的“太始”静界；结句“孤光未堕即长明”，化用《淮南子》“日月不为明而明”与禅宗“一念不生即佛”，昭示“一”非寂灭之空，乃不假外求、本自圆成的永恒觉性——此即《道德经》“天得一以清”的诗性证悟。",
                      "round": 1
                    },
                    "21046fb8-1f33-45f7-aeda-2c196471f119": {
                      "node_id": "21046fb8-1f33-45f7-aeda-2c196471f119",
                      "node_type": "cycle-start",
                      "node_name": null,
                      "status": "completed",
                      "input": {
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "message": "1",
                        "conversation_vars": {}
                      },
                      "output": {
                        "message": "1",
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                        "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd"
                      },
                      "elapsed_time": 0,
                      "token_usage": null,
                      "error": null
                    },
                    "llm_1767617560401_bsx1vhi25": {
                      "node_id": "llm_1767617560401_bsx1vhi25",
                      "node_type": "llm",
                      "node_name": "大语言模型 (LLM)-润色器",
                      "status": "completed",
                      "input": {
                        "prompt": null,
                        "messages": [
                          {
                            "role": "system",
                            "content": "请根据《咏一·次韵》  \n千峰削玉立空青，  \n一羽浮天亦自宁。  \n万籁收声归太始，  \n孤光未堕即长明。  \n\n注：本诗承原作“以一为魂”之旨，严守平水韵九青部（青、宁、明），平仄谐律。首句“千峰削玉”反衬“一羽浮天”，以极繁托极简；次句“一羽”既承“一芥”之微，更取《庄子》“鹏徙南冥”之逸气，言至微者亦可持守本然之宁。三句“万籁收声”暗应原作“千山雪落只无声”，而升华为宇宙初开的“太始”静界；结句“孤光未堕即长明”，化用《淮南子》“日月不为明而明”与禅宗“一念不生即佛”，昭示“一”非寂灭之空，乃不假外求、本自圆成的永恒觉性——此即《道德经》“天得一以清”的诗性证悟。 为主题写一首七字诗。"
                          }
                        ],
                        "config": {
                          "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                          "temperature": 0.7,
                          "max_tokens": 2000
                        }
                      },
                      "output": "《咏一·再题》  \n一芥浮空万籁停，  \n千峰影落太初青。  \n光非燃烛恒明在，  \n心不沾尘即性灵。  \n\n注：本诗续写“以一为魂”之旨，严守平水韵九青部（停、青、灵），平仄精严。首句“一芥”承原作微渺意象，而“万籁停”较“收声”更显寂然自定之境；次句倒装“千峰影落”，使苍茫山势如墨痕沉入宇宙初青，暗契《淮南子》“虚霩生宇宙，宇宙生气”之太始气象。三句翻出新境：“光非燃烛”，破除对光明之形器执取，直指《楞严经》“性觉妙明，本觉明妙”之不假缘起的本明；结句“心不沾尘即性灵”，化用六祖“本来无一物”与程颢“天地之大德曰生”，言“一”非枯寂之数，乃活泼泼的性灵朗现——此即《道德经》“昔之得一者，天清地宁”的诗性澄明。",
                      "elapsed_time": 7.1851232051849365,
                      "token_usage": {
                        "prompt_tokens": 285,
                        "completion_tokens": 281,
                        "total_tokens": 566
                      },
                      "error": null
                    },
                    "assigner_1768285417545_qsoqleflh": {
                      "node_id": "assigner_1768285417545_qsoqleflh",
                      "node_type": "assigner",
                      "node_name": "变量赋值",
                      "status": "completed",
                      "input": {
                        "config": {
                          "assignments": [
                            {
                              "value": "{{llm_1767617560401_bsx1vhi25.output}}",
                              "operation": "cover",
                              "variable_selector": "{{loop_1767617552451_hq3j342ha.poem_content}}"
                            },
                            {
                              "value": 1,
                              "operation": "add",
                              "variable_selector": "{{loop_1767617552451_hq3j342ha.round}}"
                            }
                          ]
                        }
                      },
                      "output": null,
                      "elapsed_time": 0,
                      "token_usage": null,
                      "error": null
                    }
                  },
                  "execution_id": "exec_11a80fb1cde148cb",
                  "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                  "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd",
                  "error": null,
                  "error_node": null,
                  "activate": {
                    "llm_1767617560401_bsx1vhi25": true,
                    "start_1767617465337_0djnmpk2y": true,
                    "loop_1767617552451_hq3j342ha": true,
                    "21046fb8-1f33-45f7-aeda-2c196471f119": true,
                    "llm_1767617499720_zvqwjpw3b": true,
                    "assigner_1768285417545_qsoqleflh": true
                  }
                },
                {
                  "messages": [],
                  "cycle_nodes": [
                    "loop_1767617552451_hq3j342ha"
                  ],
                  "looping": 1,
                  "node_outputs": {
                    "start_1767617465337_0djnmpk2y": {
                      "node_id": "start_1767617465337_0djnmpk2y",
                      "node_type": "start",
                      "node_name": "开始（Start）",
                      "status": "completed",
                      "input": {
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "message": "1",
                        "conversation_vars": {}
                      },
                      "output": {
                        "message": "1",
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                        "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd",
                        "topic": "",
                        "number": 0,
                        "Boolean": false
                      },
                      "elapsed_time": 0,
                      "token_usage": null,
                      "error": null
                    },
                    "llm_1767617499720_zvqwjpw3b": {
                      "node_id": "llm_1767617499720_zvqwjpw3b",
                      "node_type": "llm",
                      "node_name": "大语言模型 (LLM)-初始创作",
                      "status": "completed",
                      "input": {
                        "prompt": null,
                        "messages": [
                          {
                            "role": "system",
                            "content": "请根据1  为主题写一首七字诗。"
                          }
                        ],
                        "config": {
                          "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                          "temperature": 0.7,
                          "max_tokens": 2000
                        }
                      },
                      "output": "《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。",
                      "elapsed_time": 4.518743515014648,
                      "token_usage": {
                        "prompt_tokens": 25,
                        "completion_tokens": 165,
                        "total_tokens": 190
                      },
                      "error": null
                    },
                    "loop_1767617552451_hq3j342ha": {
                      "poem_content": "《咏一·再题》  \n一芥浮空万籁停，  \n千峰影落太初青。  \n光非燃烛恒明在，  \n心不沾尘即性灵。  \n\n注：本诗续写“以一为魂”之旨，严守平水韵九青部（停、青、灵），平仄精严。首句“一芥”承原作微渺意象，而“万籁停”较“收声”更显寂然自定之境；次句倒装“千峰影落”，使苍茫山势如墨痕沉入宇宙初青，暗契《淮南子》“虚霩生宇宙，宇宙生气”之太始气象。三句翻出新境：“光非燃烛”，破除对光明之形器执取，直指《楞严经》“性觉妙明，本觉明妙”之不假缘起的本明；结句“心不沾尘即性灵”，化用六祖“本来无一物”与程颢“天地之大德曰生”，言“一”非枯寂之数，乃活泼泼的性灵朗现——此即《道德经》“昔之得一者，天清地宁”的诗性澄明。",
                      "round": 2
                    },
                    "21046fb8-1f33-45f7-aeda-2c196471f119": {
                      "node_id": "21046fb8-1f33-45f7-aeda-2c196471f119",
                      "node_type": "cycle-start",
                      "node_name": null,
                      "status": "completed",
                      "input": {
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "message": "1",
                        "conversation_vars": {}
                      },
                      "output": {
                        "message": "1",
                        "execution_id": "exec_11a80fb1cde148cb",
                        "conversation_id": "37ee003e-cc53-47e7-930f-a436a1252dd1",
                        "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                        "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd"
                      },
                      "elapsed_time": 0,
                      "token_usage": null,
                      "error": null
                    },
                    "llm_1767617560401_bsx1vhi25": {
                      "node_id": "llm_1767617560401_bsx1vhi25",
                      "node_type": "llm",
                      "node_name": "大语言模型 (LLM)-润色器",
                      "status": "completed",
                      "input": {
                        "prompt": null,
                        "messages": [
                          {
                            "role": "system",
                            "content": "请根据《咏一·再题》  \n一芥浮空万籁停，  \n千峰影落太初青。  \n光非燃烛恒明在，  \n心不沾尘即性灵。  \n\n注：本诗续写“以一为魂”之旨，严守平水韵九青部（停、青、灵），平仄精严。首句“一芥”承原作微渺意象，而“万籁停”较“收声”更显寂然自定之境；次句倒装“千峰影落”，使苍茫山势如墨痕沉入宇宙初青，暗契《淮南子》“虚霩生宇宙，宇宙生气”之太始气象。三句翻出新境：“光非燃烛”，破除对光明之形器执取，直指《楞严经》“性觉妙明，本觉明妙”之不假缘起的本明；结句“心不沾尘即性灵”，化用六祖“本来无一物”与程颢“天地之大德曰生”，言“一”非枯寂之数，乃活泼泼的性灵朗现——此即《道德经》“昔之得一者，天清地宁”的诗性澄明。 为主题写一首七字诗。"
                          }
                        ],
                        "config": {
                          "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                          "temperature": 0.7,
                          "max_tokens": 2000
                        }
                      },
                      "output": "《咏一·三题》  \n孤光未凿太初溟，  \n一粟吞天万籁宁。  \n影堕千峰青未染，  \n心空四象白犹灵。  \n非从烛焰求明性，  \n但向尘劳见本形。  \n忽有松风穿石罅，  \n泠然吹落满山星。  \n\n注：本诗严守平水韵九青部（溟、宁、灵、形、星），其中“星”属下平声九青部异读字（《广韵》息盈切，与“灵”“宁”同部），古诗常用以协律，如王维“清溪流过碧山头，空水澄鲜一色秋。隔断红尘三十里，白云红叶两悠悠”中“悠”亦借韵通协。全诗紧扣“以一为魂”之旨：首句“孤光未凿”化《庄子·应帝王》“浑沌凿七窍而死”典，反写太初本明未分之境；次句“一粟吞天”，以微纳巨，承“一芥”而力愈雄浑；颔联“青未染”“白犹灵”，双色映照，暗喻性体离垢绝染而朗然常照；颈联直破二边——不假烛焰（破外求）、不避尘劳（破厌离），显《坛经》“佛法在世间，不离世间觉”之旨；结句松风裂石、星落满山，是“一”之活泼妙用：寂而常照，照而恒寂，恰如《道德经》“天得一以清，地得一以宁”之诗性证成。",
                      "elapsed_time": 9.531717538833618,
                      "token_usage": {
                        "prompt_tokens": 304,
                        "completion_tokens": 390,
                        "total_tokens": 694
                      },
                      "error": null
                    },
                    "assigner_1768285417545_qsoqleflh": {
                      "node_id": "assigner_1768285417545_qsoqleflh",
                      "node_type": "assigner",
                      "node_name": "变量赋值",
                      "status": "completed",
                      "input": {
                        "config": {
                          "assignments": [
                            {
                              "value": "{{llm_1767617560401_bsx1vhi25.output}}",
                              "operation": "cover",
                              "variable_selector": "{{loop_1767617552451_hq3j342ha.poem_content}}"
                            },
                            {
                              "value": 1,
                              "operation": "add",
                              "variable_selector": "{{loop_1767617552451_hq3j342ha.round}}"
                            }
                          ]
                        }
                      },
                      "output": null,
                      "elapsed_time": 0,
                      "token_usage": null,
                      "error": null
                    }
                  },
                  "execution_id": "exec_11a80fb1cde148cb",
                  "workspace_id": "d17cd62d-a725-4fc0-813b-1093f2dfdee4",
                  "user_id": "ab27a27f-072b-47e9-8bbb-1f19322debcd",
                  "error": null,
                  "error_node": null,
                  "activate": {
                    "llm_1767617560401_bsx1vhi25": true,
                    "start_1767617465337_0djnmpk2y": true,
                    "loop_1767617552451_hq3j342ha": true,
                    "21046fb8-1f33-45f7-aeda-2c196471f119": true,
                    "llm_1767617499720_zvqwjpw3b": true,
                    "assigner_1768285417545_qsoqleflh": true
                  }
                }
              ]
            }
          },
          "subContent": [
            {
              "cycle_idx": 0,
              "node_id": "21046fb8-1f33-45f7-aeda-2c196471f119",
              "node_name": null,
              "icon": "/src/assets/images/workflow/loop.png",
              "content": {
                "cycle_idx": 0,
                "input": {
                  "poem_content": "《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。",
                  "round": 0
                },
                "output": {
                  "poem_content": "《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。",
                  "round": 0
                }
              },
              "status": "completed",
              "elapsed_time": 0.0005278587341308594
            },
            {
              "cycle_idx": 0,
              "node_id": "llm_1767617560401_bsx1vhi25",
              "node_name": "大语言模型 (LLM)-润色器",
              "icon": "/src/assets/images/workflow/llm.png",
              "content": {
                "cycle_idx": 0,
                "input": {
                  "prompt": null,
                  "messages": [
                    {
                      "role": "system",
                      "content": "请根据《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。 为主题写一首七字诗。"
                    }
                  ],
                  "config": {
                    "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                    "temperature": 0.7,
                    "max_tokens": 2000
                  }
                },
                "output": "《咏一·次韵》  \n千峰削玉立空青，  \n一羽浮天亦自宁。  \n万籁收声归太始，  \n孤光未堕即长明。  \n\n注：本诗承原作“以一为魂”之旨，严守平水韵九青部（青、宁、明），平仄谐律。首句“千峰削玉”反衬“一羽浮天”，以极繁托极简；次句“一羽”既承“一芥”之微，更取《庄子》“鹏徙南冥”之逸气，言至微者亦可持守本然之宁。三句“万籁收声”暗应原作“千山雪落只无声”，而升华为宇宙初开的“太始”静界；结句“孤光未堕即长明”，化用《淮南子》“日月不为明而明”与禅宗“一念不生即佛”，昭示“一”非寂灭之空，乃不假外求、本自圆成的永恒觉性——此即《道德经》“天得一以清”的诗性证悟。"
              },
              "status": "completed",
              "elapsed_time": 6.8497374057769775
            },
            {
              "cycle_idx": 0,
              "node_id": "assigner_1768285417545_qsoqleflh",
              "node_name": "变量赋值",
              "icon": "/src/assets/images/workflow/assigner.png",
              "content": {
                "cycle_idx": 0,
                "input": {
                  "config": {
                    "assignments": [
                      {
                        "value": "{{llm_1767617560401_bsx1vhi25.output}}",
                        "operation": "cover",
                        "variable_selector": "{{loop_1767617552451_hq3j342ha.poem_content}}"
                      },
                      {
                        "value": 1,
                        "operation": "add",
                        "variable_selector": "{{loop_1767617552451_hq3j342ha.round}}"
                      }
                    ]
                  }
                },
                "output": null
              },
              "status": "completed",
              "elapsed_time": 0.0003705024719238281
            },
            {
              "cycle_idx": 1,
              "node_id": "21046fb8-1f33-45f7-aeda-2c196471f119",
              "node_name": null,
              "icon": "/src/assets/images/workflow/loop.png",
              "content": {
                "cycle_idx": 1,
                "input": {
                  "poem_content": "《咏一·次韵》  \n千峰削玉立空青，  \n一羽浮天亦自宁。  \n万籁收声归太始，  \n孤光未堕即长明。  \n\n注：本诗承原作“以一为魂”之旨，严守平水韵九青部（青、宁、明），平仄谐律。首句“千峰削玉”反衬“一羽浮天”，以极繁托极简；次句“一羽”既承“一芥”之微，更取《庄子》“鹏徙南冥”之逸气，言至微者亦可持守本然之宁。三句“万籁收声”暗应原作“千山雪落只无声”，而升华为宇宙初开的“太始”静界；结句“孤光未堕即长明”，化用《淮南子》“日月不为明而明”与禅宗“一念不生即佛”，昭示“一”非寂灭之空，乃不假外求、本自圆成的永恒觉性——此即《道德经》“天得一以清”的诗性证悟。",
                  "round": 1
                },
                "output": {
                  "poem_content": "《咏一·次韵》  \n千峰削玉立空青，  \n一羽浮天亦自宁。  \n万籁收声归太始，  \n孤光未堕即长明。  \n\n注：本诗承原作“以一为魂”之旨，严守平水韵九青部（青、宁、明），平仄谐律。首句“千峰削玉”反衬“一羽浮天”，以极繁托极简；次句“一羽”既承“一芥”之微，更取《庄子》“鹏徙南冥”之逸气，言至微者亦可持守本然之宁。三句“万籁收声”暗应原作“千山雪落只无声”，而升华为宇宙初开的“太始”静界；结句“孤光未堕即长明”，化用《淮南子》“日月不为明而明”与禅宗“一念不生即佛”，昭示“一”非寂灭之空，乃不假外求、本自圆成的永恒觉性——此即《道德经》“天得一以清”的诗性证悟。",
                  "round": 1
                }
              },
              "status": "completed",
              "elapsed_time": 0
            },
            {
              "cycle_idx": 1,
              "node_id": "llm_1767617560401_bsx1vhi25",
              "node_name": "大语言模型 (LLM)-润色器",
              "icon": "/src/assets/images/workflow/llm.png",
              "content": {
                "cycle_idx": 1,
                "input": {
                  "prompt": null,
                  "messages": [
                    {
                      "role": "system",
                      "content": "请根据《咏一·次韵》  \n千峰削玉立空青，  \n一羽浮天亦自宁。  \n万籁收声归太始，  \n孤光未堕即长明。  \n\n注：本诗承原作“以一为魂”之旨，严守平水韵九青部（青、宁、明），平仄谐律。首句“千峰削玉”反衬“一羽浮天”，以极繁托极简；次句“一羽”既承“一芥”之微，更取《庄子》“鹏徙南冥”之逸气，言至微者亦可持守本然之宁。三句“万籁收声”暗应原作“千山雪落只无声”，而升华为宇宙初开的“太始”静界；结句“孤光未堕即长明”，化用《淮南子》“日月不为明而明”与禅宗“一念不生即佛”，昭示“一”非寂灭之空，乃不假外求、本自圆成的永恒觉性——此即《道德经》“天得一以清”的诗性证悟。 为主题写一首七字诗。"
                    }
                  ],
                  "config": {
                    "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                    "temperature": 0.7,
                    "max_tokens": 2000
                  }
                },
                "output": "《咏一·再题》  \n一芥浮空万籁停，  \n千峰影落太初青。  \n光非燃烛恒明在，  \n心不沾尘即性灵。  \n\n注：本诗续写“以一为魂”之旨，严守平水韵九青部（停、青、灵），平仄精严。首句“一芥”承原作微渺意象，而“万籁停”较“收声”更显寂然自定之境；次句倒装“千峰影落”，使苍茫山势如墨痕沉入宇宙初青，暗契《淮南子》“虚霩生宇宙，宇宙生气”之太始气象。三句翻出新境：“光非燃烛”，破除对光明之形器执取，直指《楞严经》“性觉妙明，本觉明妙”之不假缘起的本明；结句“心不沾尘即性灵”，化用六祖“本来无一物”与程颢“天地之大德曰生”，言“一”非枯寂之数，乃活泼泼的性灵朗现——此即《道德经》“昔之得一者，天清地宁”的诗性澄明。"
              },
              "status": "completed",
              "elapsed_time": 7.1851232051849365
            },
            {
              "cycle_idx": 1,
              "node_id": "assigner_1768285417545_qsoqleflh",
              "node_name": "变量赋值",
              "icon": "/src/assets/images/workflow/assigner.png",
              "content": {
                "cycle_idx": 1,
                "input": {
                  "config": {
                    "assignments": [
                      {
                        "value": "{{llm_1767617560401_bsx1vhi25.output}}",
                        "operation": "cover",
                        "variable_selector": "{{loop_1767617552451_hq3j342ha.poem_content}}"
                      },
                      {
                        "value": 1,
                        "operation": "add",
                        "variable_selector": "{{loop_1767617552451_hq3j342ha.round}}"
                      }
                    ]
                  }
                },
                "output": null
              },
              "status": "completed",
              "elapsed_time": 0
            },
            {
              "cycle_idx": 2,
              "node_id": "21046fb8-1f33-45f7-aeda-2c196471f119",
              "node_name": null,
              "icon": "/src/assets/images/workflow/loop.png",
              "content": {
                "cycle_idx": 2,
                "input": {
                  "poem_content": "《咏一·再题》  \n一芥浮空万籁停，  \n千峰影落太初青。  \n光非燃烛恒明在，  \n心不沾尘即性灵。  \n\n注：本诗续写“以一为魂”之旨，严守平水韵九青部（停、青、灵），平仄精严。首句“一芥”承原作微渺意象，而“万籁停”较“收声”更显寂然自定之境；次句倒装“千峰影落”，使苍茫山势如墨痕沉入宇宙初青，暗契《淮南子》“虚霩生宇宙，宇宙生气”之太始气象。三句翻出新境：“光非燃烛”，破除对光明之形器执取，直指《楞严经》“性觉妙明，本觉明妙”之不假缘起的本明；结句“心不沾尘即性灵”，化用六祖“本来无一物”与程颢“天地之大德曰生”，言“一”非枯寂之数，乃活泼泼的性灵朗现——此即《道德经》“昔之得一者，天清地宁”的诗性澄明。",
                  "round": 2
                },
                "output": {
                  "poem_content": "《咏一·再题》  \n一芥浮空万籁停，  \n千峰影落太初青。  \n光非燃烛恒明在，  \n心不沾尘即性灵。  \n\n注：本诗续写“以一为魂”之旨，严守平水韵九青部（停、青、灵），平仄精严。首句“一芥”承原作微渺意象，而“万籁停”较“收声”更显寂然自定之境；次句倒装“千峰影落”，使苍茫山势如墨痕沉入宇宙初青，暗契《淮南子》“虚霩生宇宙，宇宙生气”之太始气象。三句翻出新境：“光非燃烛”，破除对光明之形器执取，直指《楞严经》“性觉妙明，本觉明妙”之不假缘起的本明；结句“心不沾尘即性灵”，化用六祖“本来无一物”与程颢“天地之大德曰生”，言“一”非枯寂之数，乃活泼泼的性灵朗现——此即《道德经》“昔之得一者，天清地宁”的诗性澄明。",
                  "round": 2
                }
              },
              "status": "completed",
              "elapsed_time": 0
            },
            {
              "cycle_idx": 2,
              "node_id": "llm_1767617560401_bsx1vhi25",
              "node_name": "大语言模型 (LLM)-润色器",
              "icon": "/src/assets/images/workflow/llm.png",
              "content": {
                "cycle_idx": 2,
                "input": {
                  "prompt": null,
                  "messages": [
                    {
                      "role": "system",
                      "content": "请根据《咏一·再题》  \n一芥浮空万籁停，  \n千峰影落太初青。  \n光非燃烛恒明在，  \n心不沾尘即性灵。  \n\n注：本诗续写“以一为魂”之旨，严守平水韵九青部（停、青、灵），平仄精严。首句“一芥”承原作微渺意象，而“万籁停”较“收声”更显寂然自定之境；次句倒装“千峰影落”，使苍茫山势如墨痕沉入宇宙初青，暗契《淮南子》“虚霩生宇宙，宇宙生气”之太始气象。三句翻出新境：“光非燃烛”，破除对光明之形器执取，直指《楞严经》“性觉妙明，本觉明妙”之不假缘起的本明；结句“心不沾尘即性灵”，化用六祖“本来无一物”与程颢“天地之大德曰生”，言“一”非枯寂之数，乃活泼泼的性灵朗现——此即《道德经》“昔之得一者，天清地宁”的诗性澄明。 为主题写一首七字诗。"
                    }
                  ],
                  "config": {
                    "model_id": "2699984d-23be-4817-b81c-c38682a08306",
                    "temperature": 0.7,
                    "max_tokens": 2000
                  }
                },
                "output": "《咏一·三题》  \n孤光未凿太初溟，  \n一粟吞天万籁宁。  \n影堕千峰青未染，  \n心空四象白犹灵。  \n非从烛焰求明性，  \n但向尘劳见本形。  \n忽有松风穿石罅，  \n泠然吹落满山星。  \n\n注：本诗严守平水韵九青部（溟、宁、灵、形、星），其中“星”属下平声九青部异读字（《广韵》息盈切，与“灵”“宁”同部），古诗常用以协律，如王维“清溪流过碧山头，空水澄鲜一色秋。隔断红尘三十里，白云红叶两悠悠”中“悠”亦借韵通协。全诗紧扣“以一为魂”之旨：首句“孤光未凿”化《庄子·应帝王》“浑沌凿七窍而死”典，反写太初本明未分之境；次句“一粟吞天”，以微纳巨，承“一芥”而力愈雄浑；颔联“青未染”“白犹灵”，双色映照，暗喻性体离垢绝染而朗然常照；颈联直破二边——不假烛焰（破外求）、不避尘劳（破厌离），显《坛经》“佛法在世间，不离世间觉”之旨；结句松风裂石、星落满山，是“一”之活泼妙用：寂而常照，照而恒寂，恰如《道德经》“天得一以清，地得一以宁”之诗性证成。"
              },
              "status": "completed",
              "elapsed_time": 9.531717538833618
            },
            {
              "cycle_idx": 2,
              "node_id": "assigner_1768285417545_qsoqleflh",
              "node_name": "变量赋值",
              "icon": "/src/assets/images/workflow/assigner.png",
              "content": {
                "cycle_idx": 2,
                "input": {
                  "config": {
                    "assignments": [
                      {
                        "value": "{{llm_1767617560401_bsx1vhi25.output}}",
                        "operation": "cover",
                        "variable_selector": "{{loop_1767617552451_hq3j342ha.poem_content}}"
                      },
                      {
                        "value": 1,
                        "operation": "add",
                        "variable_selector": "{{loop_1767617552451_hq3j342ha.round}}"
                      }
                    ]
                  }
                },
                "output": null
              },
              "status": "completed",
              "elapsed_time": 0
            }
          ],
          "status": "completed",
          "elapsed_time": 23.57662582397461
        },
        {
          "id": "end_1767619139811_ko97mb12l",
          "node_id": "end_1767619139811_ko97mb12l",
          "node_name": "结束（End）",
          "icon": "/src/assets/images/workflow/end.png",
          "content": {
            "input": {
              "config": {
                "output": "经过多次打磨，最终作品如下：\n{{loop_1767617552451_hq3j342ha.poem_content}} \nLLM1结果：\n{{llm_1767617499720_zvqwjpw3b.output}} "
              }
            },
            "output": "经过多次打磨，最终作品如下：\n《咏一·三题》  \n孤光未凿太初溟，  \n一粟吞天万籁宁。  \n影堕千峰青未染，  \n心空四象白犹灵。  \n非从烛焰求明性，  \n但向尘劳见本形。  \n忽有松风穿石罅，  \n泠然吹落满山星。  \n\n注：本诗严守平水韵九青部（溟、宁、灵、形、星），其中“星”属下平声九青部异读字（《广韵》息盈切，与“灵”“宁”同部），古诗常用以协律，如王维“清溪流过碧山头，空水澄鲜一色秋。隔断红尘三十里，白云红叶两悠悠”中“悠”亦借韵通协。全诗紧扣“以一为魂”之旨：首句“孤光未凿”化《庄子·应帝王》“浑沌凿七窍而死”典，反写太初本明未分之境；次句“一粟吞天”，以微纳巨，承“一芥”而力愈雄浑；颔联“青未染”“白犹灵”，双色映照，暗喻性体离垢绝染而朗然常照；颈联直破二边——不假烛焰（破外求）、不避尘劳（破厌离），显《坛经》“佛法在世间，不离世间觉”之旨；结句松风裂石、星落满山，是“一”之活泼妙用：寂而常照，照而恒寂，恰如《道德经》“天得一以清，地得一以宁”之诗性证成。 \nLLM1结果：\n《咏一》  \n孤峰独峙破苍冥，  \n一芥微身立太清。  \n万古乾坤凝此数，  \n千山雪落只无声。  \n\n注：本诗以“一”为魂，通过“孤峰”“一芥”“此数”层层递进，赋予数字哲思——既写天地间唯一性之壮美（孤峰破冥），又寓渺小个体与永恒宇宙的辩证（芥子纳太清）。末句“千山雪落只无声”，以大静写大一，雪覆千山而声息俱寂，暗合《道德经》“天得一以清”之境。平仄依平水韵，押九青部（冥、清、声）。 "
          },
          "status": "completed",
          "elapsed_time": 0.0005218982696533203
        }
      ],
      "status": "completed"
    }
  ]) // Chat message history
  const [variables, setVariables] = useState<Variable[]>([]) // Workflow input variables
  const [streamLoading, setStreamLoading] = useState(false) // SSE streaming state
  const [conversationId, setConversationId] = useState<string | null>(null) // Current conversation ID
  const [fileList, setFileList] = useState<any[]>([]) // Uploaded files
  const [message, setMessage] = useState<string | undefined>(undefined) // Current input message
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null)

  /**
   * Opens the chat drawer and loads workflow variables from the start node
   */
  const handleOpen = () => {
    setOpen(true)
    getVariables()
  }
  /**
   * Extracts variables from the workflow's start node and merges with previous values
   */
  const getVariables = () => {
    const nodes = graphRef.current?.getNodes()
    const list = nodes?.map(node => node.getData()) || []
    const startNodes = list.filter(vo => vo.type === 'start')
    if (startNodes.length) {
      const curVariables = startNodes[0].config.variables?.defaultValue

      curVariables.forEach((vo: Variable) => {
        if (typeof vo.default !== 'undefined') {
          vo.value = vo.default
        }
        const lastVo = variables.find(item => item.name === vo.name)
        if (lastVo?.value) {
          vo.value = lastVo.value
        }
      })
      setVariables(curVariables)
    }
  }
  /**
   * Closes the drawer and resets all state
   */
  const handleClose = () => {
    setOpen(false)
    setChatList([])
    setVariables([])
    setConversationId(null)
    setMessage(undefined)
    setFileList([])
  }
  /**
   * Opens the variable configuration modal
   */
  const handleEditVariables = () => {
    variableConfigModalRef.current?.handleOpen(variables)
  }
  /**
   * Saves updated variable values from the modal
   */
  const handleSave = (values: Variable[]) => {
    setVariables([...values])
  }
  /**
   * Sends a message to execute the workflow
   * 
   * Process:
   * 1. Validates required variables
   * 2. Adds user message to chat
   * 3. Initiates SSE stream for workflow execution
   * 4. Handles real-time node execution updates
   * 5. Updates chat with results or errors
   * 
   * @param msg - Optional message to send (uses state if not provided)
   */
  const handleSend = async (msg?: string) => {
    if (loading || !appId) return
    // Validate required variables before sending
    let isCanSend = true
    const params: Record<string, any> = {}
    if (variables.length > 0) {
      const needRequired: string[] = []
      variables.forEach(vo => {
        params[vo.name] = vo.value ?? vo.defaultValue

        if (vo.required && (params[vo.name] === null || params[vo.name] === undefined || params[vo.name] === '')) {
          isCanSend = false
          needRequired.push(vo.name)
        }
      })

      if (needRequired.length) {
        messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
      }
    }
    if (!isCanSend) {
      return
    }

    setLoading(true)
    const message = msg
    setChatList(prev => [...prev, {
      role: 'user',
      content: message,
      created_at: Date.now(),
    }])
    setChatList(prev => [...prev, {
      role: 'assistant',
      content: '',
      created_at: Date.now(),
      subContent: [],
    }])

    /**
     * Handles SSE stream messages from workflow execution
     * 
     * Events:
     * - message: Streaming text chunks for final output
     * - node_start: Node execution begins
     * - node_end: Node execution completes successfully
     * - node_error: Node execution fails
     * - workflow_end: Entire workflow completes
     */
    const handleStreamMessage = (data: SSEMessage[]) => {
      data.forEach(item => {
        const { chunk, conversation_id, node_id, cycle_id, cycle_idx, input, output, error, elapsed_time, status } = item.data as {
          chunk: string;
          conversation_id: string | null;
          cycle_id: string;
          cycle_idx: number;
          node_id: string;
          node_name?: string;
          input?: any;
          output?: any;
          elapsed_time?: string;
          error?: any;
          state: Record<string, any>;
          status?: 'completed' | 'failed'
        };

        const node = graphRef.current?.getNodes().find(n => n.id === node_id);
        const { name, icon } = node?.getData() || {}

        switch(item.event) {
          // Append streaming text chunks to assistant message
          case 'message':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  content: newList[lastIndex].content + chunk
                }
              }
              return newList
            })
            break
          // Track node execution start
          case 'node_start':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.id === node_id)
                if (filterIndex > -1) {
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    node_id: node_id,
                    node_name: name,
                    icon,
                    content: {},
                  }
                } else {
                  newSubContent.push({
                    id: node_id,
                    node_id: node_id,
                    node_name: name,
                    icon,
                    content: {},
                  })
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  subContent: newSubContent
                }
              }
              return newList
            })
            break
          // Update node with execution results or errors
          case 'node_end':
          case 'node_error':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.node_id === node_id)
                if (filterIndex > -1 && newSubContent[filterIndex].content) {
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    content: {
                      input,
                      output,
                      error,
                    },
                    status: status || 'completed',
                    elapsed_time
                  }
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  subContent: newSubContent
                }
              }
              return newList
            })
            break
          // Update node with subContent
          case 'cycle_item':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.id === cycle_id)
                if (filterIndex > -1) {
                  const items = newSubContent[filterIndex].subContent || []
                  items.push({
                    cycle_id,
                    cycle_idx,
                    node_id,
                    node_name: name,
                    icon,
                    content: {
                      cycle_idx,
                      input,
                      output,
                      error,
                    },
                    status: status || 'completed',
                    elapsed_time
                  })
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    subContent: [...items]
                  }
                  newList[lastIndex] = {
                    ...newList[lastIndex],
                    subContent: newSubContent
                  }
                }
              }
              return newList
            })
            break
          // Mark workflow as complete
          case 'workflow_end':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  status,
                  content: newList[lastIndex].content === '' ? null : newList[lastIndex].content
                }
              }
              return newList
            })
            setStreamLoading(false)
            setLoading(false)
            break
        }

        if (conversation_id && conversationId !== conversation_id) {
          setConversationId(conversation_id)
        }
      })
    }

    setMessage(undefined)
    setFileList([])
    const data = {
      message: message,
      variables: params,
      stream: true,
      conversation_id: conversationId,
      files: fileList.map(file => {
        if (file.url) {
          return file
        } else {
          return {
            type: file.type,
            transfer_method: 'local_file',
            upload_file_id: file.response.data.file_id
          }
        }
      })
    }
    setStreamLoading(true)
    draftRun(appId, data, handleStreamMessage)
      .catch((error) => {
        setChatList(prev => {
          const newList = [...prev]
          const lastIndex = newList.length - 1
          if (lastIndex >= 0) {
            newList[lastIndex] = {
              ...newList[lastIndex],
              status: 'failed',
              content: null,
              subContent: error.error
            }
          }
          return newList
        })
      }).finally(() => {
        setLoading(false)
        setStreamLoading(false)
      })
  }

  /**
   * Updates the current input message
   */
  const handleMessageChange = (message: string) => {
    setMessage(message)
  }
  /**
   * Handles file upload from local device
   */
  const fileChange = (file?: any) => {
    setFileList([...fileList, file])
  }
  // const handleRecordingComplete = async (file: any) => {
  //   console.log('file', file)
  // }

  /**
   * Handles dropdown menu actions for file upload
   */
  const handleShowUpload: MenuProps['onClick'] = ({ key }) => {
    switch(key) {
      case 'define':
        uploadFileListModalRef.current?.handleOpen()
        break
    }
  }
  /**
   * Adds files from remote URL modal
   */
  const addFileList = (list?: any[]) => {
    if (!list || list.length <= 0) return
    setFileList([...fileList, ...(list || [])])
  }
  /**
   * Updates the entire file list (used when removing files)
   */
  const updateFileList = (list?: any[]) => {
    setFileList([...list || []])
  }

  // Expose methods to parent component via ref
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbDrawer
      title={<div className="rb:flex rb:items-center rb:gap-2.5">
        {t('workflow.run')}
        {variables.length > 0 && <Space>
          <Button size="small" onClick={handleEditVariables}>{t('application.variable')}</Button>
        </Space>}
      </div>}
      classNames={{
        body: 'rb:p-0!'
      }}
      open={open}
      onClose={handleClose}
    >
      <ChatContent
        classNames="rb:mx-[16px] rb:pt-[24px] rb:h-[calc(100%-86px)]"
        contentClassNames="rb:max-w-[400px]!'"
        empty={<Empty url={ChatIcon} title={t('application.chatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
        data={chatList}
        streamLoading={streamLoading}
        labelPosition="bottom"
        labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
        errorDesc={t('application.ReplyException')}
        renderRuntime={(item, index) => {
          return <Runtime item={item} index={index} />
        }}
      />
      <div className="rb:relative rb:flex rb:items-center rb:gap-2.5 rb:m-4 rb:mb-1">
        <ChatInput
          message={message}
          className="rb:relative!"
          loading={loading}
          fileChange={updateFileList}
          fileList={fileList}
          onSend={handleSend}
          onChange={handleMessageChange}
        >
          <Flex justify="space-between" className="rb:flex-1">
            <Flex gap={8} align="center">
              <Dropdown
                menu={{
                  items: [
                    { key: 'define', label: t('memoryConversation.addRemoteFile') },
                    {
                      key: 'upload', label: (
                        <UploadFiles
                          fileType={['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg']}
                          onChange={fileChange}
                        />
                      )
                    },
                  ],
                  onClick: handleShowUpload
                }}
              >
                <div
                  className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/link.svg')] rb:hover:bg-[url('@/assets/images/conversation/link_hover.svg')]"
                ></div>
              </Dropdown>
            </Flex>
            {/* <Flex align="center">
              <AudioRecorder onRecordingComplete={handleRecordingComplete} />
              <Divider type="vertical" className="rb:ml-1.5! rb:mr-3!" />
            </Flex> */}
          </Flex>
        </ChatInput>
      </div>

      <VariableConfigModal
        ref={variableConfigModalRef}
        refresh={handleSave}
        variables={variables}
      />

      <UploadFileListModal
        ref={uploadFileListModalRef}
        refresh={addFileList}
      />
    </RbDrawer>
  )
})

export default Chat
