import type { Dispatch, SetStateAction, MutableRefObject } from 'react'

import type { ChatItem } from '@/components/Chat/types'
import type { SSEMessage } from '@/utils/stream'
import { getFileStatusById } from '@/api/fileStorage'
import type { NodeData } from './type'
import {
  addRunStartMessage,
  addRunEndMessage,
  appendAssistantMessage,
  appendReasoningMessage,
  applyErrorMessage,
  applyMessageId,
  applyUserMessageId,
  appendWorkflowContent,
  appendWorkflowIntervention,
  markWorkflowInterventionTimeout,
  mergeWorkflowAgentLog,
  addWorkflowNodeStart,
  updateWorkflowNodeEnd,
  updateWorkflowCycle,
  updateWorkflowEnd,
} from './helpers'

type SetChatList = Dispatch<SetStateAction<Array<ChatItem | ChatItem[]>>>

export interface AgentStreamDeps {
  conversationId: string | null;
  setConversationId: (id: string) => void;
  setChatList: SetChatList;
  setLoading: (loading: boolean) => void;
  setStreamLoading: (loading: boolean) => void;
  streamLoadingRef: MutableRefObject<boolean>;
  audioStatusMap: Record<string, string>;
  setAudioStatusMap: Dispatch<SetStateAction<Record<string, string>>>;
  audioPollingRef: MutableRefObject<Map<string, ReturnType<typeof setInterval>>>;
}

/** Builds the SSE handler for agent / chat draft runs. */
export const createAgentStreamHandler = (deps: AgentStreamDeps) => {
  const {
    conversationId, setConversationId, setChatList,
    setLoading, setStreamLoading, streamLoadingRef,
    audioStatusMap, setAudioStatusMap, audioPollingRef,
  } = deps

  const syncConversationId = (conversation_id?: string) => {
    if (conversation_id && conversationId !== conversation_id) setConversationId(conversation_id)
  }

  return (data: SSEMessage[]) => {
    data.map(item => {
      const { conversation_id, content, message_length, audio_url, citations, suggested_questions, error, message_id } = item.data as {
        conversation_id: string, content: string, message_length: number; audio_url?: string;
        citations?: NodeData['citations'];
        suggested_questions?: string[];
        error?: { message?: string; };
        message_id?: string;
      };
      // Capture the draft-run message id so like/dislike/regenerate/etc. can target it
      if (message_id) setChatList(prev => applyMessageId(prev, message_id))
      switch (item.event) {
        case 'start':
          syncConversationId(conversation_id)
          break
        case 'tool_start':
          setChatList(prev => addRunStartMessage(prev, item.data))
          break
        case 'tool_end':
          setChatList(prev => addRunEndMessage(prev, item.data))
          break
        case 'reasoning':
          if (content) {
            if (streamLoadingRef.current) {
              streamLoadingRef.current = false
              setStreamLoading(false)
            }
            setChatList(prev => appendReasoningMessage(prev, content))
          }
          syncConversationId(conversation_id)
          break
        case 'message':
          setChatList(prev => appendAssistantMessage(prev, content))
          syncConversationId(conversation_id)
          break
        case 'error':
          setChatList(prev => applyErrorMessage(prev, message_length, error))
          streamLoadingRef.current = false
          setLoading(false)
          break
        case 'end':
          if (audio_url && !audioStatusMap[audio_url]) {
            setAudioStatusMap(prev => ({
              ...prev,
              [audio_url]: 'pending'
            }))
          }
          if (audio_url) {
            setChatList(prev => appendAssistantMessage(prev, content || '', audio_url, 'pending', citations, suggested_questions))
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
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0)) {
            setChatList(prev => appendAssistantMessage(prev, content || '', audio_url, undefined, citations, suggested_questions))
          }
          setChatList(prev => applyErrorMessage(prev, message_length, error))
          streamLoadingRef.current = false
          setStreamLoading(false)
          streamLoadingRef.current = false
          setLoading(false)
          break
      }
    })
  }
}

export interface WorkflowStreamDeps {
  conversationId: string | null;
  setConversationId: (id: string) => void;
  setChatList: SetChatList;
  setStreamLoading: (loading: boolean) => void;
  setLoading: (loading: boolean) => void;
  streamLoadingRef: MutableRefObject<boolean>;
  config: any;
}

/** Builds the SSE handler for workflow draft runs. */
export const createWorkflowStreamHandler = (deps: WorkflowStreamDeps) => {
  const {
    conversationId, setConversationId, setChatList,
    setStreamLoading, setLoading, streamLoadingRef, config,
  } = deps

  return (data: SSEMessage[]) => {
    data.forEach(item => {
      const {
        execution_id,
        message_id,
        user_message_id,
        node_id,
        node_name: name,
        content, conversation_id, citations,
        rendered_content, form_fields, actions, timeout_at,
        agent_log,
      } = item.data as NodeData;
      // Capture the draft-run message id so like/dislike/regenerate/etc. can target it
      if (message_id) setChatList(prev => applyMessageId(prev, message_id))
      switch (item.event) {
        // Backfill the real user message id when the workflow run starts
        case 'workflow_start':
          setChatList(prev => applyUserMessageId(prev, user_message_id))
          break
        // Append streaming text chunks to assistant message
        case 'message':
          setChatList(prev => appendWorkflowContent(prev, content))
          break
        case 'intervention_required':
          setChatList(prev => appendWorkflowIntervention(prev, {
            execution_id, node_id, node_name: name, rendered_content, form_fields, actions, timeout_at,
          }))
          break
        case 'intervention_timeout':
          setChatList(prev => markWorkflowInterventionTimeout(prev, node_id))
          break
        // Track node execution start
        case 'node_start':
          setChatList(prev => addWorkflowNodeStart(prev, item.data as NodeData, config))
          break
        // Update node with execution results or errors
        case 'node_end':
        case 'node_error':
          setChatList(prev => updateWorkflowNodeEnd(prev, item.data as NodeData))
          break
        // Update node with subContent
        case 'cycle_item':
          setChatList(prev => updateWorkflowCycle(prev, item.data as NodeData, config))
          break
        case 'agent_log':
          setChatList(prev => mergeWorkflowAgentLog(prev, node_id, agent_log))
          break
        // Mark workflow as complete
        case 'workflow_end':
          setChatList(prev => updateWorkflowEnd(prev, item.data as NodeData))
          if (citations && citations.length > 0) {
            setChatList(prev => updateWorkflowEnd(prev, item.data as NodeData, citations))
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
}
