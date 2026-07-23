import type { Dispatch, SetStateAction, MutableRefObject } from 'react'

import type { ChatData } from '../../types'
import type { SSEMessage } from '@/utils/stream'
import { getFileStatusById } from '@/api/fileStorage'
import {
  updateAssistantReasoningMessage,
  updateAssistantMessage,
  updateErrorAssistantMessage,
  addRunStartMessage,
  addRunEndMessage,
  updateClusterAssistantMessage,
  updateClusterErrorAssistantMessage,
  applyModelMessageId,
  applyModelUserMessageId,
} from './helpers'

type UpdateChatList = Dispatch<SetStateAction<ChatData[]>>

interface CompareStreamDeps {
  updateChatList: UpdateChatList;
  setLoading: (loading: boolean) => void;
  compareLoadingRef: MutableRefObject<boolean>;
  audioStatusMap: Record<string, string>;
  setAudioStatusMap: Dispatch<SetStateAction<Record<string, string>>>;
  audioPollingRef: MutableRefObject<Map<string, ReturnType<typeof setInterval>>>;
}

/**
 * Builds the SSE handler for the multi-model comparison ("compare") run. Each
 * event targets a specific model via `model_config_id`.
 */
export const createCompareStreamHandler = (deps: CompareStreamDeps) => {
  const {
    updateChatList, setLoading, compareLoadingRef,
    audioStatusMap, setAudioStatusMap, audioPollingRef,
  } = deps

  const stopComparingSpinner = () => {
    if (compareLoadingRef.current) compareLoadingRef.current = false
  }

  return (data: SSEMessage[]) => {
    data.map(item => {
      const { model_config_id, conversation_id, content, message_length, audio_url, citations, suggested_questions, error, message_id, user_message_id } = item.data as {
        model_config_id: string; conversation_id: string; content: string; message_length: number; audio_url: string;
        input?: any;
        output?: any;
        error?: any;
        message_id?: string;
        user_message_id?: string;
        citations?: {
          document_id: string;
          file_name: string;
          knowledge_id: string;
          score: string;
        }[];
        suggested_questions?: string[];
      }

      // Capture each model's message id so like/dislike/regenerate/etc. can target it
      if (message_id) updateChatList(prev => applyModelMessageId(prev, model_config_id, message_id))

      switch (item.event) {
        case 'model_start':
          updateChatList(prev => applyModelUserMessageId(prev, model_config_id, user_message_id))
          break
        case 'model_tool_start':
          updateChatList(prev => addRunStartMessage(prev, item.data))
          break
        case 'model_tool_end':
          updateChatList(prev => addRunEndMessage(prev, item.data))
          break
        case 'model_reasoning':
          stopComparingSpinner()
          updateChatList(prev => updateAssistantReasoningMessage(prev, content, model_config_id, conversation_id))
          break
        case 'model_message':
          stopComparingSpinner()
          updateChatList(prev => updateAssistantMessage(prev, content, model_config_id, conversation_id, audio_url))
          break
        case 'model_error':
          updateChatList(prev => updateErrorAssistantMessage(prev, message_length, model_config_id, error))
          break
        case 'model_end': {
          stopComparingSpinner()
          const idToPoll = `${model_config_id}_${audio_url}`
          if (audio_url && !audioStatusMap[idToPoll]) {
            setAudioStatusMap(prev => ({ ...prev, [idToPoll]: 'pending' }))
          }
          if (audio_url) {
            updateChatList(prev => updateAssistantMessage(prev, content, model_config_id, conversation_id, audio_url, citations))
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll && !audioPollingRef.current.has(idToPoll)) {
              const timer = setInterval(() => {
                getFileStatusById(fileId)
                  .then(res => {
                    const { status } = res as { status: string }
                    if (status && status !== 'pending') {
                      clearInterval(audioPollingRef.current.get(idToPoll))
                      audioPollingRef.current.delete(idToPoll)
                      setAudioStatusMap(prev => ({ ...prev, [idToPoll]: status }))
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
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0)) {
            updateChatList(prev => updateAssistantMessage(prev, content, model_config_id, conversation_id, audio_url, citations, suggested_questions))
          }
          updateChatList(prev => updateErrorAssistantMessage(prev, message_length, model_config_id, error))
          break
        }
        case 'compare_end':
          stopComparingSpinner()
          updateChatList(prev => prev.map(chat => ({ ...chat, streamLoading: false })))
          setLoading(false)
          break
      }
    })
  }
}

interface RegenerateStreamDeps {
  /** Column being regenerated, addressed by its model config id. */
  modelConfigId?: string;
  updateChatList: UpdateChatList;
  setLoading: (loading: boolean) => void;
  compareLoadingRef: MutableRefObject<boolean>;
  audioStatusMap: Record<string, string>;
  setAudioStatusMap: Dispatch<SetStateAction<Record<string, string>>>;
  audioPollingRef: MutableRefObject<Map<string, ReturnType<typeof setInterval>>>;
}

/**
 * Builds the SSE handler for regenerating a single model column. The draft-run
 * regenerate endpoint streams agent-style events with no `model_config_id`, so
 * every update is routed to the column captured in `modelConfigId`.
 */
export const createRegenerateStreamHandler = (deps: RegenerateStreamDeps) => {
  const {
    modelConfigId, updateChatList, setLoading, compareLoadingRef,
    audioStatusMap, setAudioStatusMap, audioPollingRef,
  } = deps

  const stopComparingSpinner = () => {
    if (compareLoadingRef.current) compareLoadingRef.current = false
  }

  return (data: SSEMessage[]) => {
    data.map(item => {
      const { conversation_id, content, message_length, audio_url, citations, suggested_questions, error, message_id } = item.data as {
        conversation_id?: string; content?: string; message_length?: number; audio_url?: string;
        citations?: any[]; suggested_questions?: string[]; error?: { message?: string }; message_id?: string;
      }

      if (message_id) updateChatList(prev => applyModelMessageId(prev, modelConfigId, message_id))

      switch (item.event) {
        case 'tool_start':
          updateChatList(prev => addRunStartMessage(prev, { ...(item.data as any), model_config_id: modelConfigId }))
          break
        case 'tool_end':
          updateChatList(prev => addRunEndMessage(prev, { ...(item.data as any), model_config_id: modelConfigId }))
          break
        case 'reasoning':
          stopComparingSpinner()
          updateChatList(prev => updateAssistantReasoningMessage(prev, content, modelConfigId, conversation_id))
          break
        case 'message':
          stopComparingSpinner()
          updateChatList(prev => updateAssistantMessage(prev, content, modelConfigId, conversation_id, audio_url))
          break
        case 'error':
          updateChatList(prev => updateErrorAssistantMessage(prev, message_length || 0, modelConfigId, error))
          updateChatList(prev => prev.map(chat => ({ ...chat, streamLoading: false })))
          break
        case 'end': {
          stopComparingSpinner()
          const idToPoll = `${modelConfigId}_${audio_url}`
          if (audio_url && !audioStatusMap[idToPoll]) {
            setAudioStatusMap(prev => ({ ...prev, [idToPoll]: 'pending' }))
          }
          if (audio_url) {
            updateChatList(prev => updateAssistantMessage(prev, content, modelConfigId, conversation_id, audio_url, citations))
            const fileId = audio_url.split('/').pop()
            if (fileId && !audioPollingRef.current.has(idToPoll)) {
              const timer = setInterval(() => {
                getFileStatusById(fileId)
                  .then(res => {
                    const { status } = res as { status: string }
                    if (status && status !== 'pending') {
                      clearInterval(audioPollingRef.current.get(idToPoll))
                      audioPollingRef.current.delete(idToPoll)
                      setAudioStatusMap(prev => ({ ...prev, [idToPoll]: status }))
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
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0)) {
            updateChatList(prev => updateAssistantMessage(prev, content, modelConfigId, conversation_id, audio_url, citations, suggested_questions))
          }
          updateChatList(prev => updateErrorAssistantMessage(prev, message_length || 0, modelConfigId, error))
          updateChatList(prev => prev.map(chat => ({ ...chat, streamLoading: false })))
          setLoading(false)
          break
        }
      }
    })
  }
}

interface ClusterStreamDeps {
  updateChatList: UpdateChatList;
  setLoading: (loading: boolean) => void;
  compareLoadingRef: MutableRefObject<boolean>;
  conversationId: string | null;
  setConversationId: (id: string) => void;
}

/** Builds the SSE handler for the multi-agent cluster run. */
export const createClusterStreamHandler = (deps: ClusterStreamDeps) => {
  const { updateChatList, setLoading, compareLoadingRef, conversationId, setConversationId } = deps

  const stopComparingSpinner = () => {
    if (compareLoadingRef.current) compareLoadingRef.current = false
  }
  const syncConversationId = (conversation_id?: string) => {
    if (conversation_id && conversationId !== conversation_id) setConversationId(conversation_id)
  }

  return (data: SSEMessage[]) => {
    data.map(item => {
      const { conversation_id, content, message_length, message_id } = item.data as { conversation_id: string, content: string, message_length: number, message_id?: string }

      // Cluster runs a single column; capture its message id for message actions
      if (message_id) updateChatList(prev => applyModelMessageId(prev, undefined, message_id))

      switch (item.event) {
        case 'start':
          syncConversationId(conversation_id)
          break
        case 'message':
          stopComparingSpinner()
          updateChatList(prev => updateClusterAssistantMessage(prev, content))
          syncConversationId(conversation_id)
          break
        case 'model_end':
          stopComparingSpinner()
          updateChatList(prev => updateClusterErrorAssistantMessage(prev, message_length))
          break
        case 'compare_end':
          stopComparingSpinner()
          updateChatList(prev => prev.map(chat => ({ ...chat, streamLoading: false })))
          setLoading(false)
          break
      }
    })
  }
}
