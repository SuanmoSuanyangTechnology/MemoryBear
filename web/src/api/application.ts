/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 13:59:45 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 13:59:45 
 */
import { request } from '@/utils/request'
import type { ApplicationModalData } from '@/views/ApplicationManagement/types'
import type { Config } from '@/views/ApplicationConfig/types'
import { handleSSE, type SSEMessage } from '@/utils/stream'
import type { QueryParams } from '@/views/Conversation/types'
import type { WorkflowConfig } from '@/views/Workflow/types'

// Application list
export const getApplicationListUrl = '/apps'
export const getApplicationList = (data: Record<string, unknown>) => {
  return request.get(getApplicationListUrl, data)
}
// Get application config
export const getApplicationConfig = (id: string) => {
  return request.get(`/apps/${id}/config`)
}
// Get multi-agent config
export const getMultiAgentConfig = (id: string) => {
  return request.get(`/apps/${id}/multi-agent`)
}
// Get workflow config
export const getWorkflowConfig = (id: string) => {
  return request.get(`/apps/${id}/workflow`)
}
// Application details
export const getApplication = (id: string) => {
  return request.get(`/apps/${id}`)
}
// Update application
export const updateApplication = (id: string, values: ApplicationModalData) => {
  return request.put(`/apps/${id}`, values)
}
// Create application
export const addApplication = (values: ApplicationModalData) => {
  return request.post('/apps', values)
}
// Save agent config
export const saveAgentConfig = (app_id: string, values: Config) => {
  return request.put(`/apps/${app_id}/config`, values)
}
// Save multi-agent config
export const saveMultiAgentConfig = (app_id: string, values: Config) => {
  return request.put(`/apps/${app_id}/multi-agent`, values)
}
// Save workflow config
export const saveWorkflowConfig = (app_id: string, values: WorkflowConfig) => {
  return request.put(`/apps/${app_id}/workflow`, values)
}
// Model comparison test run
export const runCompare = (app_id: string, values: Record<string, unknown>, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE(`/apps/${app_id}/draft/run/compare`, values, onMessage)
}
// Test run
export const draftRun = (app_id: string, values: Record<string, unknown>, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE(`/apps/${app_id}/draft/run`, values, onMessage)
}
// Delete application
export const deleteApplication = (app_id: string) => {
  return request.delete(`/apps/${app_id}`)
}
// Release version list
export const getReleaseList = (app_id: string) => {
  return request.get(`/apps/${app_id}/releases`)
}
// Publish release
export const publishRelease = (app_id: string, values: Record<string, unknown>) => {
  return request.post(`/apps/${app_id}/publish`, values)
}
// Rollback release
export const rollbackRelease = (app_id: string, version: string) => {
  return request.post(`/apps/${app_id}/rollback/${version}`)
}
// Share release
export const shareRelease = (app_id: string, release_id: string) => {
  return request.post(`/apps/${app_id}/releases/${release_id}/share`, {
    "is_enabled": true,
    "require_password": false,
    "allow_embed": true
  })
}
// Get conversation history
export const getConversationHistory = (share_token: string, data: { page: number; pagesize: number }) => {
  return request.get(`/public/share/conversations`, data, {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem(`shareToken_${share_token}`)}`
    }
  })
}
// Send conversation
export const sendConversation = (values: QueryParams, onMessage: (data: SSEMessage[]) => void, shareToken: string) => {
  return handleSSE(`/public/share/chat`, values, onMessage, {
    headers: {
      'Authorization': `Bearer ${shareToken}`
    }
  })
}
// Get conversation details
export const getConversationDetail = (share_token: string, conversation_id: string) => {
  return request.get(`/public/share/conversations/${conversation_id}`, {}, {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem(`shareToken_${share_token}`)}`
    }
  })
}
// Get share token
export const getShareToken = (share_token: string, user_id: string) => {
  return request.post(`/public/share/${share_token}/token`, { user_id })
}
// Copy application
export const copyApplication = (app_id: string, new_name: string) => {
  return request.post(`/apps/${app_id}/copy?new_name=${new_name}`)
}
// Data statistics
export const getAppStatistics = (app_id: string, data: { start_date: number; end_date: number; }) => {
  return request.get(`/apps/${app_id}/statistics`, data)
}
