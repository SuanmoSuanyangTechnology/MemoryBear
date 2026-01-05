import { request } from '@/utils/request'
import type { AiPromptForm } from '@/views/ApplicationConfig/types'

export const createPromptSessions = () => {
  return request.post(`/prompt/sessions`)
}
export const getPrompt = (session_id: string) => {
  return request.get(`/prompt/sessions/${session_id}`)
}
export const updatePromptMessages = (session_id: string, data: AiPromptForm) => {
  return request.post(`/prompt/sessions/${session_id}/messages`, data)
}