/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-13 17:27:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 21:48:30
 */
import { type FC, useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { App } from 'antd'
import clsx from 'clsx'
import dayjs from 'dayjs'

import ChatIcon from '@/assets/images/application/chat.png'
import { draftRun } from '@/api/application'

import Empty from '@/components/Empty'
import Chat from '@/components/Chat'
import RbCard from '@/components/RbCard/Card'
import ChatToolbar, { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import Runtime from '@/views/Workflow/components/Chat/Runtime'
import { nodeLibrary } from '@/views/Workflow/constant'

import type { ChatItem } from '@/components/Chat/types'
import type { WorkflowConfig } from '@/views/Workflow/types'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { TestChatProps } from './type'
import type { SSEMessage } from '@/utils/stream'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import { getFileStatusById } from '@/api/fileStorage'
import { replaceVariables } from '@/views/ApplicationConfig/Agent'

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
  status?: 'completed' | 'failed';
  audio_url?: string;
  citations?: {
    document_id: string;
    file_name: string;
    knowledge_id: string;
    score: string;
  }[]
}

const TestChat: FC<TestChatProps> = ({
  application,
  config
}) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const toolbarRef = useRef<ChatToolbarRef>(null)

  const [loading, setLoading] = useState(false)
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [streamLoading, setStreamLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | undefined>(undefined)
  const [fileList, setFileList] = useState<any[]>([])
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)
  const [variables, setVariables] = useState<Variable[]>([])
  
  const audioPollingRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())
  const streamLoadingRef = useRef(false)
  const [audioStatusMap, setAudioStatusMap] = useState<Record<string, string>>({})

  useEffect(() => {
    getVariables()
  }, [application, JSON.stringify(config)])

  useEffect(() => {
    return () => {
      audioPollingRef.current.forEach(timer => clearInterval(timer))
      audioPollingRef.current.clear()
    }
  }, [])

  const getVariables = () => {
    if (!application || !config) return

    setFeatures(config?.features || {} as FeaturesConfigForm)


    if (config?.features?.opening_statement?.enabled && config?.features?.opening_statement?.statement && config?.features?.opening_statement?.statement.trim() !== '') {
      setChatList(prev => [...prev, {
        role: 'assistant',
        created_at: Date.now(),
        content: config?.features?.opening_statement?.statement,
        meta_data: {
          suggested_questions: config?.features?.opening_statement?.suggested_questions || []
        }
      }])
    }
  
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

    toolbarRef.current?.setVariables([...initVariables])
    setVariables([...initVariables])
  }

  const addUserMessage = (message: string, files: any[]) => {
    setChatList(prev => [...prev, {
      role: 'user',
      content: message,
      created_at: Date.now(),
      meta_data: {
        files
      },
    }])
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

  const updateAssistantMessage = (content: string, audio_url?: string, audio_status?: string, citations?: NodeData['citations']) => {
    setChatList(prev => {
      const newList = [...prev]
      const lastMsg = newList[newList.length - 1]
      if (lastMsg?.role === 'assistant') {
        newList[newList.length - 1] = {
          ...lastMsg,
          content: lastMsg.content + content,
          meta_data: {
            ...(lastMsg.meta_data || {}),
            audio_url: audio_url || lastMsg.meta_data?.audio_url,
            audio_status: audio_status || lastMsg.meta_data?.audio_status,
            citations: citations || lastMsg.meta_data?.citations
          }
        }
      }
      return newList
    })
  }
  const updateAssistantReasoningMessage = (content: string) => {
    if (!content) return
    if (streamLoadingRef.current) {
      streamLoadingRef.current = false
      setStreamLoading(false)
    }
    setChatList(prev => {
      const newList = [...prev]
      const lastMsg = newList[newList.length - 1]
      if (lastMsg?.role === 'assistant') {
        newList[newList.length - 1] = {
          ...lastMsg,
          meta_data: {
            ...(lastMsg.meta_data || {}),
            reasoning_content: (lastMsg.meta_data?.reasoning_content || '') + content
          }
        }
      }
      return newList
    })
  }

  const updateErrorAssistantMessage = (message_length: number) => {
    if (message_length > 0) return
    setChatList(prev => {
      const newList = [...prev]
      const lastMsg = newList[newList.length - 1]
      if (lastMsg.role === 'assistant') {
        lastMsg.content = null
      }
      return newList
    })
  }

  const buildVariableParams = (variables: Variable[]) => {
    let isCanSend = true
    const params: Record<string, any> = {}
    if (variables?.length > 0) {
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
    return { isCanSend, params }
  }

  const handleSend = (msg?: string) => {
    if (loading || !application || !((message && message?.trim() !== '') || (msg && msg?.trim() !== ''))) return
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    const variables = toolbarRef.current?.getVariables() || []
    const { isCanSend, params } = buildVariableParams(variables)
    if (!isCanSend) return

    addUserMessage((msg || message) as string, files)
    setMessage(undefined)
    toolbarRef.current?.setFiles([])
    setFileList([])
    addAssistantMessage()
    streamLoadingRef.current = true
    setStreamLoading(true)
    setLoading(true)

    draftRun(
      application.id,
      formatParams((msg || message) as string, conversationId, files, params),
      handleStreamMessage
    )
      .catch(() => {
        updateErrorAssistantMessage(0)
        setLoading(false)
      })
      .finally(() => {
        setLoading(false)
        streamLoadingRef.current = false
        setStreamLoading(false)
      })
  }

  useEffect(() => {
    if (!Object.keys(audioStatusMap).length) return
    setChatList(prev => prev.map(msg => {
      if (msg.role === 'assistant' && msg.meta_data?.audio_url && audioStatusMap[msg.meta_data.audio_url]) {
        return {
          ...msg,
          meta_data: {
            ...msg.meta_data,
            audio_status: audioStatusMap[msg.meta_data.audio_url]
          }
        }
      }
      return msg
    }))
  }, [audioStatusMap, chatList.length])

  const handleStreamMessage = (data: SSEMessage[]) => {
    data.map(item => {
      const { conversation_id, content, message_length, audio_url, citations } = item.data as {
        conversation_id: string, content: string, message_length: number; audio_url?: string;
        citations?: NodeData['citations']
      };
      switch (item.event) {
        case 'start':
          if (conversation_id && conversationId !== conversation_id) setConversationId(conversation_id)
          break
        case 'reasoning':
          updateAssistantReasoningMessage(content)
          if (conversation_id && conversationId !== conversation_id) setConversationId(conversation_id)
          break
        case 'message':
          updateAssistantMessage(content)
          if (conversation_id && conversationId !== conversation_id) setConversationId(conversation_id)
          break
        case 'end':
          if (audio_url && !audioStatusMap[audio_url]) {
            setAudioStatusMap(prev => ({
              ...prev,
              [audio_url]: 'pending'
            }))
          }
          if (audio_url) {
            updateAssistantMessage(content || '', audio_url, 'pending')
            const { file_id } = item.data as { file_id?: string }
            const idToPoll = file_id || audio_url || ''
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll && !audioPollingRef.current.has(idToPoll)) {
              const timer = setInterval(() => {
                getFileStatusById(fileId)
                  .then(res => {
                    const { status } = res as { status: string }
                    if (status && status !== 'pending') {
                      setAudioStatusMap(prev => ({
                        ...prev,
                        [audio_url]: status
                      }))
                      clearInterval(audioPollingRef.current.get(idToPoll))
                      audioPollingRef.current.delete(idToPoll)
                    }
                  })
                  .catch(() => {
                    clearInterval(audioPollingRef.current.get(idToPoll))
                    audioPollingRef.current.delete(idToPoll)
                  })
              }, 2000)
              audioPollingRef.current.set(idToPoll, timer)
            }
          }
          if (citations && citations.length > 0) {
            updateAssistantMessage(content, audio_url, undefined, citations)
          }
          updateErrorAssistantMessage(message_length)
          streamLoadingRef.current = false
          setStreamLoading(false)
          break
      }
    })
  }

  const handleWorkflowSend = (msg?: string) => {
    if (loading || !application || !((message && message?.trim() !== '') || (msg && msg?.trim() !== ''))) return
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    const variables = toolbarRef.current?.getVariables() || []
    const { isCanSend, params } = buildVariableParams(variables)
    if (!isCanSend) return

    setLoading(true)
    addUserMessage((msg || message) as string, files)
    addAssistantMessage()
    toolbarRef.current?.setFiles([])
    setFileList([])
    setMessage(undefined)
    setStreamLoading(true)
    streamLoadingRef.current = true

    draftRun(
      application.id,
      formatParams((msg || message) as string, conversationId, files, params),
      handleWorkflowStreamMessage
    )
      .catch((error) => {
        const errorInfo = JSON.parse(error.message)
        setChatList(prev => {
          const newList = [...prev]
          const lastIndex = newList.length - 1
          if (lastIndex >= 0) {
            newList[lastIndex] = { ...newList[lastIndex], status: 'failed', content: null, subContent: errorInfo.error }
          }
          return newList
        })
      })
      .finally(() => {
        setLoading(false)
        setStreamLoading(false)
        streamLoadingRef.current = false
      })
  }

  const handleWorkflowStreamMessage = (data: SSEMessage[]) => {
    data.forEach(item => {
      const { content, conversation_id, citations } = item.data as NodeData;
      switch (item.event) {
      // Append streaming text chunks to assistant message
        case 'message':
          setChatList(prev => {
            const newList = [...prev]
            const lastIndex = newList.length - 1
            if (lastIndex >= 0) {
              newList[lastIndex] = { ...newList[lastIndex], content: newList[lastIndex].content + content }
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
          if (citations && citations.length > 0) {
            updateWorkflowEndMessage(item.data as NodeData, citations)
          }
          setStreamLoading(false)
          streamLoadingRef.current = false
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

  const updateWorkflowEndMessage = (data: NodeData, citations?: NodeData['citations']) => {
    const { error, status } = data;
    setChatList(prev => {
      const newList = [...prev]
      const lastIndex = newList.length - 1
      if (lastIndex >= 0) {
        newList[lastIndex] = {
          ...newList[lastIndex],
          status,
          error,
          content: newList[lastIndex].content === '' ? null : newList[lastIndex].content,
          meta_data: {
            ...newList[lastIndex].meta_data || {},
            citations
          }
        }
      }
      return newList
    })
  }

  useEffect(() => {
    const opening_statement = features?.opening_statement

    if (opening_statement?.enabled && opening_statement?.statement && opening_statement?.statement.trim() !== '') {
      const assistantMsg: ChatItem = {
        role: 'assistant',
        content: replaceVariables(opening_statement.statement, variables as any),
        meta_data: {
          suggested_questions: opening_statement?.suggested_questions
        }
      }
      setChatList(prev => {
        prev[0] = assistantMsg
        return [...prev]
      })
    }
  }, [chatList.length, features?.opening_statement, variables])

  return (
    <div className="rb:w-250 rb:mx-auto rb:h-full">
      <RbCard
        title={t('application.test')}
        headerClassName="rb:min-h-[56px]!"
        className="rb:h-full!"
        bodyClassName="rb:h-[calc(100%-56px)]! rb:overflow-y-auto rb:px-3! rb:py-0!"
      >
        <Chat
          empty={<Empty url={ChatIcon} title={t('application.testChatEmpty')} isNeedSubTitle={false} size={[240, 200]} />}
          contentClassName={clsx(`rb:mx-[16px] rb:pt-[24px]`, {
            'rb:h-[calc(100%-140px)]': !fileList.length,
            'rb:h-[calc(100%-208px)]': !!fileList.length,
          })}
          data={chatList}
          streamLoading={streamLoading}
          loading={loading}
          onChange={setMessage}
          onSend={application?.type === 'workflow' ? handleWorkflowSend : handleSend}
          fileList={fileList}
          fileChange={(list) => {
            setFileList(list || [])
            toolbarRef.current?.setFiles(list || [])
          }}
          labelFormat={(item) => item.role === 'user' ? t('application.you') : dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
          errorDesc={t('application.ReplyException')}
          renderRuntime={application?.type === 'workflow' ? (item, index) => <Runtime item={item} index={index} /> : undefined}
        >
          <ChatToolbar
            ref={toolbarRef}
            features={features}
            onFilesChange={setFileList}
            onVariablesChange={setVariables}
          />
        </Chat>
      </RbCard>
    </div>
  )
}

export default TestChat
