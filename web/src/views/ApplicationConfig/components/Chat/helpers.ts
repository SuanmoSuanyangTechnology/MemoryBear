import type { ChatData } from '../../types'
import type { ChatItem } from '@/components/Chat/types'
import type { Variable } from '../VariableList/types'
import { mapLastVersion } from '@/components/Chat/utils/messageVersions'

/**
 * Pure reducers backing the multi-model comparison chat panel. Each operates on
 * the `ChatData[]` list (one entry per compared model) and returns a new array,
 * keeping the component free of inline state-mutation logic. A single model's
 * `list` may hold plain messages or regenerate version arrays
 * (`Array<ChatItem | ChatItem[]>`), so streaming updates go through
 * `mapLastVersion` to target the latest version safely.
 */

/**
 * Locates the column owning the given model config id. Falls back to the sole
 * column when no id is supplied (the single-column cluster mode).
 */
const findModelIndex = (prev: ChatData[], model_config_id?: string): number => {
  if (model_config_id) return prev.findIndex(item => item.model_config_id === model_config_id)
  return prev.length === 1 ? 0 : -1
}

/**
 * Applies an updater to the latest version of the targeted model's last message,
 * optionally refreshing that column's conversation id.
 */
const mapModelLastVersion = (
  prev: ChatData[],
  model_config_id: string | undefined,
  updater: (item: ChatItem) => ChatItem,
  conversation_id?: string,
): ChatData[] => {
  const idx = findModelIndex(prev, model_config_id)
  if (idx === -1) return prev
  const next = [...prev]
  next[idx] = {
    ...next[idx],
    ...(conversation_id !== undefined ? { conversation_id } : {}),
    list: mapLastVersion(next[idx].list || [], updater),
  }
  return next
}

/** Appends a user message to every model's conversation. */
export const addUserMessage = (prev: ChatData[], message: string, files: any[]): ChatData[] => {
  const newUserMessage: ChatItem = {
    role: 'user',
    content: message,
    created_at: Date.now(),
    meta_data: { files },
  }
  return prev.map(item => ({
    ...item,
    list: [...(item.list || []), newUserMessage],
  }))
}

/** Appends an empty assistant placeholder to every model's conversation. */
export const addAssistantMessage = (prev: ChatData[]): ChatData[] => {
  const assistantMessage: ChatItem = {
    role: 'assistant',
    content: '',
    created_at: Date.now(),
  }
  return prev.map(item => ({
    ...item,
    list: [...(item.list || []), assistantMessage],
  }))
}

/**
 * Captures a draft-run message id onto the targeted model's last assistant
 * version so like / dislike / regenerate / etc. can address it.
 */
export const applyModelMessageId = (
  prev: ChatData[],
  model_config_id?: string,
  id?: string,
): ChatData[] => {
  if (!id) return prev
  const idx = findModelIndex(prev, model_config_id)
  if (idx === -1) return prev
  const list = prev[idx].list || []
  const lastEntry = list[list.length - 1]
  const lastItem = Array.isArray(lastEntry) ? lastEntry[lastEntry.length - 1] : lastEntry
  if (!lastItem || lastItem.role !== 'assistant' || lastItem.id === id) return prev
  const next = [...prev]
  next[idx] = { ...next[idx], list: mapLastVersion(list, current => ({ ...current, id })) }
  return next
}

/**
 * Backfills the real id onto the most recent pending user message of the targeted
 * model column (fired by `model_start`).
 */
export const applyModelUserMessageId = (
  prev: ChatData[],
  model_config_id?: string,
  id?: string,
): ChatData[] => {
  if (!id) return prev
  const idx = findModelIndex(prev, model_config_id)
  if (idx === -1) return prev
  const list = prev[idx].list || []
  for (let i = list.length - 1; i >= 0; i--) {
    const entry = list[i]
    if (!Array.isArray(entry) && entry.role === 'user') {
      if (entry.id === id) return prev
      const newList = list.map((it, j) =>
        j === i && !Array.isArray(it) ? { ...it, id } : it
      )
      const next = [...prev]
      next[idx] = { ...next[idx], list: newList }
      return next
    }
  }
  return prev
}

/** Appends streaming reasoning content to the targeted model's last assistant message. */
export const updateAssistantReasoningMessage = (
  prev: ChatData[],
  content?: string,
  model_config_id?: string,
  conversation_id?: string,
): ChatData[] => {
  if (!content || !model_config_id) return prev
  return mapModelLastVersion(prev, model_config_id, lastMsg =>
    lastMsg.role === 'assistant'
      ? {
        ...lastMsg,
        meta_data: {
          reasoning_content: (lastMsg.meta_data?.reasoning_content || '') + (content || ''),
        },
      }
      : lastMsg,
    conversation_id,
  )
}

/** Appends streaming content / audio / citations to the targeted model's last assistant message. */
export const updateAssistantMessage = (
  prev: ChatData[],
  content?: string,
  model_config_id?: string,
  conversation_id?: string,
  audio_url?: string,
  citations?: any[],
  suggested_questions?: string[],
): ChatData[] => {
  if ((!content && !audio_url && (!citations || citations?.length < 1) && (!suggested_questions || suggested_questions?.length < 1)) || !model_config_id) return prev
  return mapModelLastVersion(prev, model_config_id, lastMsg =>
    lastMsg.role === 'assistant'
      ? {
        ...lastMsg,
        content: lastMsg.content + (content || ''),
        meta_data: {
          ...(lastMsg.meta_data || {}),
          ...(audio_url !== undefined ? { audio_url, audio_status: 'pending' } : {}),
          citations: citations || lastMsg.meta_data?.citations,
          suggested_questions: suggested_questions || lastMsg.meta_data?.suggested_questions,
        },
      }
      : lastMsg,
    conversation_id,
  )
}

/** Marks the targeted model's last assistant message as completed / failed. */
export const updateErrorAssistantMessage = (
  prev: ChatData[],
  message_length: number,
  model_config_id?: string,
  error?: { message?: string; },
): ChatData[] => {
  if (!model_config_id && !error) return prev
  return mapModelLastVersion(prev, model_config_id, lastMsg => {
    if (message_length > 0) {
      const subContent = lastMsg.subContent || []
      const hasFailed = subContent.some(vo => vo.status === 'failed')
      return { ...lastMsg, status: hasFailed ? 'failed' : 'completed' }
    }
    if (!lastMsg.meta_data?.reasoning_content || lastMsg.meta_data?.reasoning_content.length === 0) {
      return { ...lastMsg, status: 'failed', content: error?.message || lastMsg.content }
    }
    return lastMsg
  })
}

/** Adds a tool-run start placeholder to the targeted model's last assistant message. */
export const addRunStartMessage = (prev: ChatData[], data: any): ChatData[] => {
  const { model_config_id, conversation_id, name, step_id, input } = data
  return mapModelLastVersion(prev, model_config_id, lastMsg =>
    lastMsg.role === 'assistant'
      ? {
        ...lastMsg,
        subContent: step_id ?[
          ...(lastMsg.subContent || []),
          {
            node_id: `${name}`,
            node_type: 'tool',
            node_name: name,
            status: 'pending',
            content: { input },
          },
        ] : lastMsg.subContent || [],
      }
      : lastMsg,
    conversation_id,
  )
}

/** Finalises the tool-run detail on the targeted model's last assistant message. */
export const addRunEndMessage = (prev: ChatData[], data: any): ChatData[] => {
  const { model_config_id, conversation_id, meta, output, error } = data
  return mapModelLastVersion(prev, model_config_id, lastMsg => {
    if (lastMsg.role !== 'assistant') return lastMsg
    const lastSubContent = lastMsg.subContent || []
    const lastSubContentItem = lastSubContent[lastSubContent.length - 1]
    let sourceList: any[] = []
    if (meta?.sources?.length > 0 && (meta?.tool_type === 'knowledge_retrieval' || meta?.tool_type === 'skill')) {
      const groupedSources = meta?.sources.reduce((acc: any, source: any) => {
        const key = source.knowledge_name || source.knowledge_id || source.name || source.id || 'default'
        if (!acc[key]) {
          acc[key] = { ...source, name: source.knowledge_name || source.name, contentList: [source.content] }
        } else {
          acc[key].contentList.push(source.content)
        }
        return acc
      }, {})
      sourceList = Object.values(groupedSources).map((group: any) => ({
        ...lastSubContentItem,
        status: error ? 'failed' : 'completed',
        node_name: group.name,
        content: {
          input: lastSubContentItem.content?.input || '',
          output: group.contentList.join('\n') || output,
          error,
        },
      }))
    } else if (meta?.sources?.length > 0) {
      sourceList = meta?.sources?.map((source: any) => ({
        ...lastSubContentItem,
        status: error ? 'failed' : 'completed',
        name: source.name || 'default',
        content: {
          input: lastSubContentItem.content?.input || '',
          output: source.content || output,
          error,
        },
      }))
    } else {
      sourceList = [{
        ...lastSubContentItem,
        status: error ? 'failed' : 'completed',
        content: {
          input: lastSubContentItem.content?.input || '',
          output: output || '',
          error,
        },
      }]
    }
    return {
      ...lastMsg,
      subContent: [
        ...lastSubContent.slice(0, -1),
        ...sourceList,
      ],
    }
  }, conversation_id)
}

/* --------------------------------- cluster -------------------------------- */

/** Appends streaming content to the first (cluster) conversation's last assistant message. */
export const updateClusterAssistantMessage = (prev: ChatData[], content?: string): ChatData[] => {
  if (!content) return prev
  const next = [...prev]
  next[0] = {
    ...next[0],
    list: mapLastVersion(next[0].list || [], lastMsg =>
      lastMsg.role === 'assistant' ? { ...lastMsg, content: lastMsg.content + content } : lastMsg,
    ),
  }
  return next
}

/** Nulls the cluster conversation's last assistant message on error. */
export const updateClusterErrorAssistantMessage = (prev: ChatData[], message_length: number): ChatData[] => {
  if (message_length > 0) return prev
  const next = [...prev]
  next[0] = {
    ...next[0],
    list: mapLastVersion(next[0].list || [], lastMsg =>
      lastMsg.role === 'assistant' ? { ...lastMsg, content: null } : lastMsg,
    ),
  }
  return next
}

/** Applies polled audio statuses onto every assistant message that owns an audio url. */
export const applyAudioStatus = (prev: ChatData[], audioStatusMap: Record<string, string>): ChatData[] =>
  prev.map(item => ({
    ...item,
    list: item.list?.map(entry => {
      const apply = (msg: ChatItem): ChatItem => {
        const id = `${item.model_config_id}_${msg.meta_data?.audio_url}`
        if (msg.role === 'assistant' && msg.meta_data?.audio_url && audioStatusMap[id]) {
          return {
            ...msg,
            meta_data: {
              ...msg.meta_data,
              audio_status: audioStatusMap[id],
            },
          }
        }
        return msg
      }
      return Array.isArray(entry) ? entry.map(apply) : apply(entry)
    }),
  }))

/**
 * Validates required chat variables, returning the collected params and the
 * names of any required-but-empty variables.
 */
export const collectVariableParams = (chatVariables?: Variable[]) => {
  const params: Record<string, any> = {}
  const needRequired: string[] = []
  if (chatVariables && chatVariables.length > 0) {
    chatVariables.forEach(vo => {
      params[vo.name] = vo.value
      if (vo.required && (params[vo.name] === null || params[vo.name] === undefined || params[vo.name] === '')) {
        needRequired.push(vo.name)
      }
    })
  }
  return { isCanSend: needRequired.length === 0, params, needRequired }
}

/** Maps attachments to the draft-run file payload shape. */
export const formatFiles = (files: any[]) =>
  files.map(file => {
    if (file.url) {
      return file
    }
    return {
      type: file.type,
      transfer_method: 'local_file',
      upload_file_id: file.response.data.file_id,
    }
  })
