import { request } from '@/utils/request'
import type { AiPromptForm } from '@/views/ApplicationConfig/types'
import { handleSSE, type SSEMessage } from '@/utils/stream'

export const createPromptSessions = () => {
  return request.post(`/prompt/sessions`)
}
export const getPrompt = (session_id: string) => {
  return request.get(`/prompt/sessions/${session_id}`)
}
export const updatePromptMessages = (session_id: string, data: AiPromptForm, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE(`/prompt/sessions/${session_id}/messages`, data, onMessage)
}