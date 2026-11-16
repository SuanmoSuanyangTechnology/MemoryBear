import type { ChatItem } from '@/components/Chat/types'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { WorkflowConfig } from '@/views/Workflow/types'
import { nodeLibrary } from '@/views/Workflow/constant'
import { triggerParams } from '@/views/Workflow/components/Properties/hooks/useVariableList'
import type { NodeData } from './type'

type ChatList = Array<ChatItem | ChatItem[]>

// Shared version-array helpers (single source of truth).
import { mapLastVersion, flattenChatList } from '@/components/Chat/utils/messageVersions'
export { mapLastVersion, flattenChatList }

/**
 * Builds the draft-run request payload from the current message and attachments.
 * `message` is omitted for message-less runs (pure workflow).
 */
export const formatParams = (
  message: string | undefined,
  conversation_id: string | null,
  files: any[] = [],
  variables: Record<string, any>,
  trigger_payload: Record<string, any> = {},
) => {
  return {
    message: message || undefined,
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
    variables: Object.keys(variables).length > 0 ? variables : undefined,
    trigger_payload: Object.keys(trigger_payload).length > 0 ? trigger_payload : undefined
  }
}

/**
 * Collects input parameters from the configured variables and reports any
 * required-but-empty variable names. Webhook trigger variables are grouped into
 * the `trigger_payload` object (e.g. `body.question` -> `{ body: { question } }`).
 */
export const collectVariableParams = (variables: Variable[]) => {
  const params: Record<string, any> = {}
  const trigger_payload: Record<string, any> = {}
  const needRequired: string[] = []
  if (variables?.length > 0) {
    variables.filter(vo => !vo.nodeType).forEach(vo => {
      params[vo.name] = vo.value ?? vo.defaultValue
      if (vo.required && (params[vo.name] === null || params[vo.name] === undefined || params[vo.name] === '')) {
        needRequired.push(vo.name)
      }
    })
    variables.filter(vo => vo.nodeType === 'webhook').forEach(vo => {
      const [root, ...rest] = vo.name.split('.')
      if (!root || !rest.length) return
      trigger_payload[root] = {
        ...(trigger_payload[root] || {}),
        [rest.join('.')]: vo.value,
      }
    })
  }
  return { isCanSend: needRequired.length === 0, params, trigger_payload, needRequired }
}

/** Normalizes a trigger config param list (wrapped `defaultValue` or plain array). */
const getTriggerParamList = (config: Record<string, any>, key: string): any[] => {
  const value = config?.[key]
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.defaultValue)) return value.defaultValue
  return []
}

/** Collects webhook trigger input variables from the workflow's trigger nodes. */
export const collectWebhookVariables = (nodes: WorkflowConfig['nodes'] = []): Variable[] => {
  const list: Variable[] = []
  nodes.filter(node => node.type === 'trigger').forEach(node => {
    const config = (node.config || {}) as Record<string, any>
    const triggerType = config.trigger_type?.defaultValue ?? config.trigger_type
    if (triggerType !== 'webhook') return
    Object.keys(triggerParams).forEach(key => {
      getTriggerParamList(config, key).forEach(param => {
        if (!param?.name) return
        list.push({
          name: `${triggerParams[key]}.${param.name}`,
          ui_type: 'paragraph',
          type: param.type || 'string',
          required: !!param.required,
          nodeType: 'webhook',
        })
      })
    })
  })
  return list
}

/**
 * Computes the initial variable list for the trial run based on app type.
 */
export const computeInitVariables = (type: string | undefined, config: any): Variable[] => {
  let initVariables: Variable[] = []
  switch (type) {
    // pure workflow shares the workflow input contract: start node + webhook triggers
    case 'workflow':
    case 'pure_workflow': {
      const { nodes } = config as WorkflowConfig;
      const startNodes = nodes.filter(vo => vo.type === 'start')
      if (startNodes.length) {
        const curVariables = (startNodes[0].config.variables || []) as Variable[]
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
      if (type === 'pure_workflow') {
        initVariables = [...initVariables, ...collectWebhookVariables(nodes)]
      }
      break
    }
    case 'agent':
      initVariables = config.variables as Variable[]
      break
    case 'multi_agent':
      initVariables = config.supervisor_config?.variables || []
      break
  }
  return initVariables
}

/** Resolves a workflow node's display name / type / icon from the config. */
export const resolveWorkflowNode = (config: any, node_id: string) => {
  const { nodes } = config as WorkflowConfig
  const node = nodes.find(n => n.id === node_id);
  const { name, type } = node || {}
  const icon = nodeLibrary.flatMap(g => g.nodes).find(n => n.type === type)?.icon
  return { name, type, icon }
}

/* -------------------------------------------------------------------------- */
/*                          Chat list pure reducers                           */
/* -------------------------------------------------------------------------- */

export const addUserMessage = (prev: ChatList, message: string, files: any[]): ChatList => [
  ...prev,
  {
    role: 'user',
    content: message,
    created_at: Date.now(),
    meta_data: { files },
  },
]

export const addAssistantMessage = (prev: ChatList, type?: string): ChatList => [
  ...prev,
  {
    role: 'assistant',
    content: '',
    created_at: Date.now(),
    subContent: type?.includes('workflow') ? [] : undefined,
  },
]

/** Records the draft-run message id onto the latest assistant version. */
export const applyMessageId = (prev: ChatList, id?: string): ChatList => {
  if (!id) return prev
  const lastEntry = prev[prev.length - 1]
  const lastItem = Array.isArray(lastEntry) ? lastEntry[lastEntry.length - 1] : lastEntry
  if (!lastItem || lastItem.role !== 'assistant' || lastItem.id === id) return prev
  return mapLastVersion(prev, (current) => ({ ...current, id }))
}

/** Backfills the real id onto the most recent pending user message. */
export const applyUserMessageId = (prev: ChatList, id?: string): ChatList => {
  if (!id) return prev
  for (let i = prev.length - 1; i >= 0; i--) {
    const entry = prev[i]
    if (!Array.isArray(entry) && entry.role === 'user') {
      if (entry.id === id) return prev
      return prev.map((it, idx) =>
        idx === i && !Array.isArray(it) ? { ...it, id } : it
      )
    }
  }
  return prev
}

export const appendAssistantMessage = (
  prev: ChatList,
  content: string,
  audio_url?: string,
  audio_status?: string,
  citations?: NodeData['citations'],
  suggested_questions?: string[] | undefined,
): ChatList =>
  mapLastVersion(prev, (lastMsg) => {
    if (lastMsg?.role !== 'assistant') return lastMsg
    return {
      ...lastMsg,
      content: lastMsg.content + content,
      meta_data: {
        ...(lastMsg.meta_data || {}),
        audio_url: audio_url || lastMsg.meta_data?.audio_url,
        audio_status: audio_status || lastMsg.meta_data?.audio_status,
        citations: citations || lastMsg.meta_data?.citations,
        suggested_questions: suggested_questions || lastMsg.meta_data?.suggested_questions
      }
    }
  })

export const appendReasoningMessage = (prev: ChatList, content: string): ChatList =>
  mapLastVersion(prev, (lastMsg) => {
    if (lastMsg?.role !== 'assistant') return lastMsg
    return {
      ...lastMsg,
      meta_data: {
        ...(lastMsg.meta_data || {}),
        reasoning_content: (lastMsg.meta_data?.reasoning_content || '') + content
      }
    }
  })

export const applyErrorMessage = (
  prev: ChatList,
  message_length: number,
  error?: { message?: string; msg?: string; },
): ChatList => {
  if (message_length > 0) {
    return mapLastVersion(prev, (lastMsg) => {
      const subContent = lastMsg.subContent || []
      const hasFailed = subContent.some(vo => vo.status === 'failed')
      return { ...lastMsg, status: hasFailed ? 'failed' : 'completed' }
    })
  }
  return mapLastVersion(prev, (lastMsg) => {
    if (lastMsg.role !== 'assistant') return lastMsg
    const errorMsg = error?.msg || error?.message
    return {
      ...lastMsg,
      content: errorMsg || lastMsg.content || null,
      status: errorMsg ? 'failed' : 'completed',
    }
  })
}

export const addRunStartMessage = (prev: ChatList, data: any): ChatList => {
  const { name, input } = data || {}
  return mapLastVersion(prev, (lastMsg) => {
    if (lastMsg?.role !== 'assistant') return lastMsg
    return {
      ...lastMsg,
      subContent: [
        ...(lastMsg.subContent || []),
        {
          node_id: `${name}`,
          node_type: 'tool',
          node_name: name,
          status: 'pending',
          content: { input }
        }
      ]
    }
  })
}

export const addRunEndMessage = (prev: ChatList, data: any): ChatList => {
  const { meta, output, error } = data || {}
  return mapLastVersion(prev, (lastMsg) => {
    if (lastMsg?.role !== 'assistant') return lastMsg
    const lastSubContent = lastMsg.subContent || []
    const lastSubContentItem = lastSubContent[lastSubContent.length - 1]
    let sourceList: any[] = []
    if (meta?.sources?.length > 0 && (meta?.tool_type === 'knowledge_retrieval' || meta?.tool_type === 'skill')) {
      const groupedSources = meta?.sources.reduce((acc: any, source: any) => {
        const key = source.knowledge_name || source.knowledge_id || source.name || source.id || 'default';
        if (!acc[key]) {
          acc[key] = { ...source, name: source.knowledge_name || source.name, contentList: [source.content] };
        } else {
          acc[key].contentList.push(source.content);
        }
        return acc;
      }, {});
      sourceList = Object.values(groupedSources).map((group: any) => ({
        ...lastSubContentItem,
        status: error ? 'failed' : 'completed',
        node_name: group.name,
        content: {
          input: lastSubContentItem.content?.input || '',
          output: group.contentList.join('\n') || output,
          error
        }
      }));
    } else if (meta?.sources?.length > 0) {
      sourceList = meta?.sources?.map((source: any) => ({
        ...lastSubContentItem,
        status: error ? 'failed' : 'completed',
        name: source.name || 'default',
        content: {
          input: lastSubContentItem.content?.input || '',
          output: source.content || output,
          error
        }
      }));
    } else {
      sourceList = [{
        ...lastSubContentItem,
        status: error ? 'failed' : 'completed',
        content: {
          input: lastSubContentItem.content?.input || '',
          output: output || '',
          error
        }
      }]
    }
    return {
      ...lastMsg,
      subContent: [
        ...lastSubContent.slice(0, -1),
        ...sourceList
      ]
    }
  })
}

/* --------------------------- workflow reducers ---------------------------- */

export const appendWorkflowContent = (prev: ChatList, content: string): ChatList =>
  mapLastVersion(prev, (current) => ({ ...current, content: current.content + content }))

export const appendWorkflowIntervention = (
  prev: ChatList,
  data: Pick<NodeData, 'execution_id' | 'node_id' | 'node_name' | 'rendered_content' | 'form_fields' | 'actions' | 'timeout_at'>,
): ChatList => {
  const { execution_id, node_id, node_name: name, rendered_content, form_fields, actions, timeout_at } = data
  return mapLastVersion(prev, (current) => ({
    ...current,
    meta_data: {
      ...current.meta_data,
      waiting_human: true
    },
    interventions: [
      ...(current.interventions || []),
      {
        execution_id,
        node_id: node_id,
        node_name: name,
        rendered_content,
        form_fields: form_fields || [],
        actions: actions || [],
        timeout_at,
      }
    ]
  }))
}

export const markWorkflowInterventionTimeout = (prev: ChatList, node_id: string): ChatList =>
  mapLastVersion(prev, (current) => {
    const interventions = current.interventions || []
    if (interventions.length === 0) return current
    const filterIndex = interventions.findIndex(item => item.node_id === node_id)
    if (filterIndex < 0) return current
    return {
      ...current,
      interventions: interventions.map((it, idx) =>
        idx === filterIndex
          ? { ...it, resolved_action_id: '__timeout__', resolved_kind: 'timeout' }
          : it
      ),
    }
  })

export const mergeWorkflowAgentLog = (prev: ChatList, node_id: string, agent_log: any): ChatList =>
  mapLastVersion(prev, (current) => {
    const newSubContent = [...(current.subContent || [])]
    const filterIndex = newSubContent.findIndex(vo => vo.node_id === node_id)
    if (filterIndex < 0) return current
    const lastAgentLog = newSubContent[filterIndex].agent_log || {}
    const lastIterations: { index: number;[key: string]: any; }[] = lastAgentLog?.iterations || []
    const newIterations: { index: number;[key: string]: any; }[] = agent_log?.iterations || []

    const indexMap = new Map<number, typeof lastIterations[0]>()
    lastIterations.forEach(item => indexMap.set(item.index, item))
    newIterations.forEach(item => indexMap.set(item.index, item))
    const mergedIterations = Array.from(indexMap.values()).sort((a, b) => a.index - b.index)

    newSubContent[filterIndex] = {
      ...newSubContent[filterIndex],
      agent_log: {
        ...lastAgentLog,
        meta: agent_log?.meta || {},
        iterations: mergedIterations,
      }
    }
    return { ...current, subContent: newSubContent }
  })

export const addWorkflowNodeStart = (prev: ChatList, data: NodeData, config: any): ChatList => {
  const { node_id } = data;
  const { name, type, icon } = resolveWorkflowNode(config, node_id)
  return mapLastVersion(prev, (current) => {
    const newSubContent = [...(current.subContent || [])]
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
    return { ...current, subContent: newSubContent }
  })
}

export const updateWorkflowNodeEnd = (prev: ChatList, data: NodeData): ChatList => {
  const { node_id, input, output, process, error, elapsed_time, status } = data;
  return mapLastVersion(prev, (current) => {
    const newSubContent = [...(current.subContent || [])]
    const filterIndex = newSubContent.findIndex(vo => vo.node_id === node_id)
    if (filterIndex > -1 && newSubContent[filterIndex].content) {
      newSubContent[filterIndex] = {
        ...newSubContent[filterIndex],
        content: { input, output, process, error },
        status: status || 'completed',
        elapsed_time
      }
    }
    return { ...current, subContent: newSubContent }
  })
}

export const updateWorkflowCycle = (prev: ChatList, data: NodeData, config: any): ChatList => {
  const { node_id, cycle_id, cycle_idx, input, output, process, error, elapsed_time, status, agent_log } = data;
  const { name, type, icon } = resolveWorkflowNode(config, node_id)
  return mapLastVersion(prev, (current) => {
    const newSubContent = [...(current.subContent || [])]
    const filterIndex = newSubContent.findIndex(vo => vo.id === cycle_id)
    if (filterIndex < 0) return current
    const items = newSubContent[filterIndex].subContent || []
    const lastAgentLog = newSubContent[filterIndex].agent_log || {}
    items.push({
      cycle_id,
      cycle_idx,
      node_id,
      node_name: name,
      node_type: type,
      icon,
      content: { cycle_idx, input, output, process, error },
      status: status || 'completed',
      elapsed_time
    })
    newSubContent[filterIndex] = {
      ...newSubContent[filterIndex],
      subContent: [...items],
      agent_log: {
        ...lastAgentLog,
        meta: agent_log?.meta || {},
        iterations: [
          ...(lastAgentLog?.iterations || []),
          ...agent_log?.iterations || []
        ],
      }
    }
    return { ...current, subContent: newSubContent }
  })
}

export const updateWorkflowEnd = (prev: ChatList, data: NodeData, citations?: NodeData['citations']): ChatList => {
  const { error, status } = data;
  return mapLastVersion(prev, (current) => ({
    ...current,
    status,
    content: current.content === '' ? null : current.content,
    meta_data: {
      ...current.meta_data || {},
      citations,
      error,
    }
  }))
}

export const applyWorkflowSendError = (prev: ChatList, subContent: any): ChatList =>
  mapLastVersion(prev, (current) => ({ ...current, status: 'failed', content: null, subContent }))

export const applyInterventionResolved = (
  prev: ChatList,
  fieldValues: Record<string, string>,
  actionId: string,
): ChatList =>
  mapLastVersion(prev, (current) => {
    const interventions = current.interventions || []
    if (interventions.length === 0) return current
    // Update the resolved info on the last intervention
    return {
      ...current,
      interventions: [
        ...interventions.slice(0, -1),
        {
          ...interventions[interventions.length - 1],
          resolved_form_data: fieldValues,
          resolved_action_id: actionId,
        }
      ],
      meta_data: {
        ...current.meta_data,
        waiting_human: false
      }
    }
  })
