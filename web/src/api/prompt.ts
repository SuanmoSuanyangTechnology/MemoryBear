import { request } from '@/utils/request'
import type { AiPromptForm } from '@/views/ApplicationConfig/types'
import type { PromptReleaseData } from '@/views/Prompt/types'
import { handleSSE, type SSEMessage } from '@/utils/stream'

// Create session
export const createPromptSessions = () => {
  return request.post(`/prompt/sessions`)
}
// Get prompt optimization
export const updatePromptMessages = (session_id: string, data: AiPromptForm, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE(`/prompt/sessions/${session_id}/messages`, data, onMessage)
}
// Prompt release list
export const getPromptReleaseListUrl = '/prompt/releases/list'
export const getPromptReleaseList = () => {
  return request.get(getPromptReleaseListUrl)
}
// Save prompt
export const savePrompt = (data: PromptReleaseData) => {
  return request.post('/prompt/releases', data)
}
// Delete prompt
export const deletePrompt = (prompt_id: string) => {
  return request.delete(`/prompt/releases/${prompt_id}`)
}