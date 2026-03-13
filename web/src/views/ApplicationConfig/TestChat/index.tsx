import { type FC, useState, useRef, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { App, Flex, Dropdown, type MenuProps, Divider, Form, Space } from 'antd'
import { SettingOutlined } from '@ant-design/icons'
import clsx from 'clsx'
import dayjs from 'dayjs'

import ChatIcon from '@/assets/images/application/chat.png'

import VariableConfigModal from '@/views/Workflow/components/Chat/VariableConfigModal'
import { draftRun } from '@/api/application';

import Empty from '@/components/Empty'
import Chat from '@/components/Chat'
import AudioRecorder from '@/components/AudioRecorder'
import RbCard from '@/components/RbCard/Card'
import UploadFiles from '@/views/Conversation/components/FileUpload'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import Runtime from '@/views/Workflow/components/Chat/Runtime';
import { nodeLibrary } from '@/views/Workflow/constant'
// import ButtonCheckbox from '@/components/ButtonCheckbox';

// import MemoryFunctionIcon from '@/assets/images/conversation/memoryFunction.svg'
// import OnlineIcon from '@/assets/images/conversation/online.svg'
// import OnlineCheckedIcon from '@/assets/images/conversation/onlineChecked.svg'
// import MemoryFunctionCheckedIcon from '@/assets/images/conversation/memoryFunctionChecked.svg'

import type { ChatItem } from '@/components/Chat/types'
import type { VariableConfigModalRef, WorkflowConfig } from '@/views/Workflow/types'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { TestChatProps } from './type';
import type { UploadFileListModalRef } from '@/views/Conversation/types'
import type { SSEMessage } from '@/utils/stream'

const formatParams = (message: string, conversation_id: string | null, files: any[] = [], variables: Record<string, any>) => {
  return {
    message,
    conversation_id,
    stream: true,
    files: files.map(file => {
      if (file.url) {
        return file
      } else {
        return {
          type: file.type,
          transfer_method: 'local_file',
          upload_file_id: file.response.data.file_id
        }
      }
    }),
    variables: Object.keys(variables).length > 0 ? variables : undefined
  }
}

interface NodeData {
  content: string;
  conversation_id: string | null;
  cycle_id: string;
  cycle_idx: number;
  node_id: string;
  node_name?: string;
  node_type?: string;
  input?: any;
  output?: any;
  elapsed_time?: string;
  error?: any;
  state: Record<string, any>;
  status?: 'completed' | 'failed'
}

interface FormData {
  files: any[];
  variables: Variable[]
}
const TestChat: FC<TestChatProps> = ({
  application,
  config
}) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null)

  const [loading, setLoading] = useState(false) // Send button loading state
  const [chatList, setChatList] = useState<ChatItem[]>([]) // Chat message history
  const [streamLoading, setStreamLoading] = useState(false) // SSE streaming state
  const [conversationId, setConversationId] = useState<string | null>(null) // Current conversation ID
  const [message, setMessage] = useState<string | undefined>(undefined) // Current input message
  const [form] = Form.useForm<FormData>()
  const queryValues = Form.useWatch([], form)

  useEffect(() => {
    getVariables()
  }, [application, config])

  const getVariables = () => {
    if (!application || !config) return

    let initVariables: Variable[] = []

    switch (application.type) {
      case 'workflow':
        const { nodes } = config as WorkflowConfig;
        const startNodes = nodes.filter(vo => vo.type === 'start')
        if (startNodes.length) {
          const curVariables = startNodes[0].config.variables as Variable[]
      
            curVariables.forEach((vo) => {
              if (typeof vo.default !== 'undefined') {
                vo.value = vo.default
              }
              const lastVo = curVariables.find(item => item.name === vo.name)
              if (lastVo?.value) {
                vo.value = lastVo.value
              }
            })
            initVariables = curVariables
          }
        break
      case 'agent':
        initVariables = config.variables as Variable[]
        break
    }

    form.setFieldValue('variables', [...initVariables])
  }

  /**
   * Opens the variable configuration modal
   */
  const handleEditVariables = () => {
    variableConfigModalRef.current?.handleOpen(queryValues.variables)
  }
  /**
   * Saves updated variable values from the modal
   */
  const handleSave = (values: Variable[]) => {
    form.setFieldValue('variables', [...values])
  }
  /**
   * Handles file upload from local device
   */
  const fileChange = (file?: any) => {
    form.setFieldValue('files', [...(queryValues.files || []), file])
  }
  const handleRecordingComplete = async (file: any) => {
    form.setFieldValue('files', [...(queryValues.files || []), file])
  }

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
    form.setFieldValue('files', [...(queryValues.files || []), ...(list || [])])
  }
  /**
   * Updates the entire file list (used when removing files)
   */
  const updateFileList = (list?: any[]) => {
    form.setFieldValue('files', [...list || []])
  }
  const isNeedVariableConfig = useMemo(() => {
    return queryValues?.variables.some(vo => vo.required && (vo.value === null || vo.value === undefined || vo.value === ''))
  }, [queryValues?.variables])

  const addUserMessage = (message: string, files: any[]) => {
    const newUserMessage: ChatItem = {
      role: 'user',
      content: message,
      created_at: Date.now(),
      files
    };
    setChatList(prev => [...prev, newUserMessage])
  }
  const addAssistantMessage = () => {
    const { type } = application || {}
    setChatList(prev => [...prev, {
      role: 'assistant',
      content: '',
      created_at: Date.now(),
      subContent: type === 'workflow' ? [] : undefined,
    }])
  }

  const updateAssistantMessage = (content: string) => {
    setChatList(prev => {
      let newList = [...prev]
      const lastMsg = newList[newList.length - 1]
      if (lastMsg.role === 'assistant') {
        lastMsg.content += content
      }
      return newList
    })
  }
  const updateErrorAssistantMessage = (message_length: number) => {
    if (message_length > 0) return
    setChatList(prev => {
      let newList = [...prev]
      const lastMsg = newList[newList.length - 1]
      if (lastMsg.role === 'assistant') {
        lastMsg.content = null
      }
      return newList
    })
  }
  const handleSend = () => {
    if (loading || !application || !message || !message?.trim()) return
    // Validate required variables before sending
    const { variables, files } = queryValues;
    let isCanSend = true
    const params: Record<string, any> = {}
    if (variables && variables.length > 0) {
      const needRequired: string[] = []
      variables.forEach(vo => {
        params[vo.name] = vo.value

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
      setLoading(false)
      return
    }
    addUserMessage(message, files)
    setMessage(undefined)
    form.setFieldValue('files', [])
    addAssistantMessage()
    setStreamLoading(true)
    setLoading(true)

    draftRun(
      application.id,
      formatParams(message, conversationId, files, params),
      handleStreamMessage
    )
      .catch(() => {
        setLoading(false)
      })
      .finally(() => {
        setLoading(false)
        setStreamLoading(false)
      })
  }
  const handleStreamMessage = (data: SSEMessage[]) => {
    data.map(item => {
      const { conversation_id, content, message_length } = item.data as { conversation_id: string, content: string, message_length: number };

      switch (item.event) {
        case 'start':
          if (conversation_id && conversationId !== conversation_id) {
            setConversationId(conversation_id);
          }
          break
        case 'message':
          updateAssistantMessage(content)
          if (conversation_id && conversationId !== conversation_id) {
            setConversationId(conversation_id);
          }
          break;
        case 'end':
          updateErrorAssistantMessage(message_length)
          setStreamLoading(false)
          break;
      }
    })
  };

  const handleWorkflowSend = () => {
    if (loading || !application || !message || !message?.trim()) return

    // Validate required variables before sending
    const { variables, files } = queryValues;
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
    addUserMessage(message, files)
    addAssistantMessage()
    form.setFieldsValue({
      files: [],
    })

    setMessage(undefined)
    setStreamLoading(true)
    draftRun(
      application.id,
      formatParams(message, conversationId, files, params),
      handleWorkflowStreamMessage
    )
      .catch((error) => {
        console.log('draftRun error', error)
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
  const handleWorkflowStreamMessage = (data: SSEMessage[]) => {
    data.forEach(item => {
      const { content, conversation_id } = item.data as NodeData;

      switch (item.event) {
        // Append streaming text chunks to assistant message
        case 'message':
          setChatList(prev => {
            const newList = [...prev]
            const lastIndex = newList.length - 1
            if (lastIndex >= 0) {
              newList[lastIndex] = {
                ...newList[lastIndex],
                content: newList[lastIndex].content + content
              }
            }
            return newList
          })
          break
        // Track node execution start
        case 'node_start':
          addWorkflowNodeStartMessage(item.data as NodeData)
          break
        // Update node with execution results or errors
        case 'node_end':
        case 'node_error':
          updateWorkflowNodeEndMessage(item.data as NodeData)
          break
        // Update node with subContent
        case 'cycle_item':
          updateWorkflowCycleMessage(item.data as NodeData)
          break
        // Mark workflow as complete
        case 'workflow_end':
          updateWorkflowEndMessage(item.data as NodeData)
          setStreamLoading(false)
          setLoading(false)
          break
      }

      if (conversation_id && conversationId !== conversation_id) {
        setConversationId(conversation_id)
      }
    })
  }
  const addWorkflowNodeStartMessage = (data: NodeData) => {
    const { node_id } = data;
    const { nodes } = config as WorkflowConfig

    const node = nodes.find(n => n.id === node_id);
    const { name, type } = node || {}
    const icon = nodeLibrary.flatMap(g => g.nodes).find(n => n.type === type)?.icon
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
            node_type: type,
            icon,
            content: {},
          }
        } else {
          newSubContent.push({
            id: node_id,
            node_id: node_id,
            node_name: name,
            node_type: type,
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
  }
  const updateWorkflowNodeEndMessage = (data: NodeData) => {
    const { node_id, input, output, error, elapsed_time, status } = data;
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
  }
  const updateWorkflowCycleMessage = (data: NodeData) => {
    const { node_id, cycle_id, cycle_idx, input, output, error, elapsed_time, status } = data;
    const { nodes } = config as WorkflowConfig

    const node = nodes.find(n => n.id === node_id);
    const { name, type } = node || {}
    const icon = nodeLibrary.flatMap(g => g.nodes).find(n => n.type === type)?.icon
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
            node_type: type,
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
  }
  const updateWorkflowEndMessage = (data: NodeData) => {
    const { error, status } = data as {
      content: string;
      conversation_id: string | null;
      cycle_id: string;
      cycle_idx: number;
      node_id: string;
      node_name?: string;
      node_type?: string;
      input?: any;
      output?: any;
      elapsed_time?: string;
      error?: any;
      state: Record<string, any>;
      status?: 'completed' | 'failed'
    };
    setChatList(prev => {
      const newList = [...prev]
      const lastIndex = newList.length - 1
      if (lastIndex >= 0) {
        newList[lastIndex] = {
          ...newList[lastIndex],
          status,
          error,
          content: newList[lastIndex].content === '' ? null : newList[lastIndex].content,
        }
      }
      return newList
    })
  }

  console.log('queryValues', queryValues)
  return (
    <div className="rb:w-250 rb:p-3 rb:mx-auto">
      <RbCard
        title={t('application.test')}
        headerClassName="rb:min-h-[56px]!"
        className="rb:h-[calc(100vh-88px)]!"
        bodyClassName="rb:h-[calc(100%-56px)]! rb:overflow-y-auto rb:px-3! rb:py-0!"
      >
        <Chat
          empty={<Empty url={ChatIcon} title={t('application.testChatEmpty')} isNeedSubTitle={false} size={[240, 200]} />}
          contentClassName={clsx(`rb:mx-[16px] rb:pt-[24px]`, {
            'rb:h-[calc(100%-140px)]': !queryValues?.files?.length,
            'rb:h-[calc(100%-208px)]': !!queryValues?.files?.length,
          })}
          data={chatList}
          streamLoading={streamLoading}
          loading={loading}
          onChange={setMessage}
          onSend={application?.type === 'workflow' ? handleWorkflowSend : handleSend}
          fileList={queryValues?.files || []}
          fileChange={updateFileList}
          labelFormat={(item) => item.role === 'user' ? t('application.you') : dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
          errorDesc={t('application.ReplyException')}
          renderRuntime={application?.type === 'workflow' ? (item, index) => {
            return <Runtime item={item} index={index} />
          } : undefined}
        >
          <Form form={form}>
            <Flex justify="space-between" className="rb:flex-1">
              <Space size={8} align="center">
                <Form.Item name="files" noStyle>
                  <Dropdown
                    menu={{
                      items: [
                        { key: 'define', label: t('memoryConversation.addRemoteFile') },
                        {
                          key: 'upload', label: (
                            <UploadFiles
                              onChange={fileChange}
                            />
                          )
                        },
                      ],
                      onClick: handleShowUpload
                    }}
                  >
                    <Flex align="center" justify="center" className="rb:size-7 rb:cursor-pointer rb:rounded-[14px] rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6]">
                      <div
                        className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/link.svg')]"
                      ></div>
                    </Flex>
                  </Dropdown>
                </Form.Item>
                {/* <Form.Item name="web_search" valuePropName="checked" className="rb:mb-0!">
                  <ButtonCheckbox
                    icon={OnlineIcon}
                    checkedIcon={OnlineCheckedIcon}
                  >
                    {t(`memoryConversation.web_search`)}
                  </ButtonCheckbox>
                </Form.Item>
                <Tooltip title={t(`memoryConversation.memory`)}></Tooltip>
                <Form.Item name="memory" valuePropName="checked" className="rb:mb-0!">
                  <ButtonCheckbox
                    icon={MemoryFunctionIcon}
                    checkedIcon={MemoryFunctionCheckedIcon}
                    cicle={true}
                  >
                  </ButtonCheckbox>
                </Form.Item> */}
                <Form.Item name="variables" className="rb:mb-0!" hidden={!queryValues?.variables?.length}>
                  <div
                    className={clsx("rb:flex rb:items-center rb:border rb:rounded-lg rb:px-2 rb:text-[12px] rb:h-6 rb:cursor-pointer rb:hover:bg-[#F0F3F8] rb:text-[#212332]", {
                      'rb:border-[#FF5D34] rb:text-[#FF5D34]': isNeedVariableConfig,
                      'rb:border-[#DFE4ED]': !isNeedVariableConfig,
                    })}
                    onClick={handleEditVariables}
                  >
                    <SettingOutlined className="rb:mr-1" />
                    {t(`memoryConversation.variableConfig`)}
                  </div>
                </Form.Item>
              </Space>
              <Space size={8} align="center">
                <AudioRecorder
                  onRecordingComplete={handleRecordingComplete}
                />
                <Divider type="vertical" className="rb:ml-0! rb:mr-2!" />
              </Space>
            </Flex>
          </Form>
        </Chat>

        <VariableConfigModal
          ref={variableConfigModalRef}
          refresh={handleSave}
        />

        <UploadFileListModal
          ref={uploadFileListModalRef}
          refresh={addFileList}
        />
      </RbCard>
    </div>
  )
}

export default TestChat
