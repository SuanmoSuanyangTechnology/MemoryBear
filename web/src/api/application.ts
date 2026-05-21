/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 13:59:45 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-20 15:55:16
 */
import { request } from '@/utils/request'
import type { ApplicationModalData } from '@/views/ApplicationManagement/types'
import type { Config, AppSharingForm, AnnotationSettingForm, AnnotationForm } from '@/views/ApplicationConfig/types'
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
export const runCompare = (app_id: string, values: Record<string, unknown>, onMessage?: (data: SSEMessage[]) => void, onAbort?: (abort: () => void) => void) => {
  return handleSSE(`/apps/${app_id}/draft/run/compare`, values, onMessage, undefined, onAbort)
}
// Test run
export const draftRun = (app_id: string, values: Record<string, unknown>, onMessage?: (data: SSEMessage[]) => void, onAbort?: (abort: () => void) => void) => {
  return handleSSE(`/apps/${app_id}/draft/run`, values, onMessage, undefined, onAbort)
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
export const sendConversation = (values: QueryParams, onMessage: (data: SSEMessage[]) => void, shareToken: string, onAbort?: (abort: () => void) => void) => {
  return handleSSE(`/public/share/chat`, values, onMessage, {
    headers: {
      'Authorization': `Bearer ${shareToken}`
    }
  }, onAbort)
}
// Get conversation details
export const getConversationDetail = (share_token: string, conversation_id: string) => {
  return request.get(`/public/share/conversations/${conversation_id}`, {}, {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem(`shareToken_${share_token}`)}`
    }
  })
}
// Like/Dislike AI response
export const feedbackMessage = (share_token: string, message_id: string, data: { feedback_type: 'like' | 'dislike' }) => {
  console.log('token', share_token, `Bearer ${localStorage.getItem(`shareToken_${share_token}`)}`)
  return request.post(`/public/share/messages/${message_id}/feedback`, data, {
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
export const copyApplication = (app_id: string, new_name?: string) => {
  return request.post(`/apps/${app_id}/copy`, { new_name })
}
// Data statistics
export const getAppStatistics = (app_id: string, data: { start_date: number; end_date: number; }) => {
  return request.get(`/apps/${app_id}/statistics`, data)
}
// Upload workflow and analyze compatibility
export const importWorkflow = (formData: FormData) => {
  return request.uploadFile(`/apps/workflow/import`, formData)
}
// Complete workflow import
export const completeImportWorkflow = (data: { temp_id: string; name?: string; description?: string }) => {
  return request.post(`/apps/workflow/import/save`, data)
}
// Get experience config
export const getExperienceConfig = (share_token: string) => {
  return request.get(`/public/share/config`, {}, {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem(`shareToken_${share_token}`)}`
    }
  })
}
// Get workspace API call statistics
export const getWorkspaceApiStatistics = (data: { start_date: number; end_date: number; }) => {
  return request.get(`/apps/workspace/api-statistics`, data)
}
// Export application
export const appExport = (app_id: string, appName: string, data?: { release_id: string }) => {
  return request.getDownloadFile(`/apps/${app_id}/export`, `${appName}.yml`, data)
}
// Import application
export const appImport = (formData: FormData) => {
  return request.uploadFile(`/apps/import`, formData)
}

// Share application
export const appSharing = (app_id: string, data: AppSharingForm) => {
  return request.post(`/apps/${app_id}/share`, data)
}
// Get my shared application records
export const mySharedOutList = () => {
  return request.get(`/apps/my-shared-out`)
}
// Get sharing records for a specific application
export const getAppShares = (app_id: string) => {
  return request.get(`/apps/${app_id}/shares`)
}
// Cancel a single share (source side operation)
export const cancelShare = (app_id: string, target_workspace_id?: string) => {
  return request.delete(`/apps/${app_id}/share/${target_workspace_id}`)
}
// Cancel all shares under a workspace (source side operation)
export const cancelSpaceShare = (target_workspace_id?: string) => {
  return request.delete(`/apps/share/${target_workspace_id}`)
}
// Application conversation logs
export const getAppLogsUrl = (app_id: string) => `/apps/${app_id}/logs`
// Get full conversation message history
export const getAppLogDetail = (app_id: string, conversation_id: string) => {
  return request.get(`/apps/${app_id}/logs/${conversation_id}`)
}
// Reset agent model config to default
export const resetAppModelConfig = (app_id: string) => {
  return request.get(`/apps/${app_id}/model/parameters/default`)
}
// Single node test run
export const nodeRun = (app_id: string, node_id: string, values: Record<string, unknown>) => {
  return request.post(`/apps/${app_id}/workflow/nodes/${node_id}/run`, values)
}
// Configure annotation settings
export const updateAnnotationsSettings = (app_id: string, data: AnnotationSettingForm) => {
  return request.put(`/apps/${app_id}/annotations/settings`, data)
}
// Get current application annotation settings
export const getAnnotationsSettings = (app_id: string) => {
  return request.get(`/apps/${app_id}/annotations/settings`)
}
// Get annotation list
export const getAnnotationsListUrl = (app_id: string) => `/apps/${app_id}/annotations`
export const getAnnotationsList = (app_id: string, data: { search?: string, page?: number, pagesize?: number }) => {
  return request.get(getAnnotationsListUrl(app_id), data)
}
// Create QA annotation pair
export const createAnnotations = (app_id: string, data: AnnotationForm) => {
  return request.post(`/apps/${app_id}/annotations`, data)
}
// Edit annotation pair
export const editAnnotations = (app_id: string, annotation_id: string, data: AnnotationForm) => {
  return request.put(`/apps/${app_id}/annotations/${annotation_id}`, data)
}
// Delete annotation
export const deleteAnnotations = (app_id: string, annotation_id: string) => {
  return request.delete(`/apps/${app_id}/annotations/${annotation_id}`)
}
// Batch delete all annotations
export const deleteAllAnnotations = (app_id: string) => {
  return request.delete(`/apps/${app_id}/annotations`)
}
// Batch export annotations (CSV / JSON)
export const exportAnnotation = (app_id: string, format: 'csv' | 'jsonl') => {
  return request.getDownloadFile(`/apps/${app_id}/annotations/export?format=${format}`, `annotations.${format}`)
}
// Batch import annotations (CSV)
export const importAnnotation = (app_id: string, formData: FormData) => {
  return request.uploadFile(`/apps/${app_id}/annotations/import`, formData)
}
// Get annotation hit history
export const getAnnotationHitHistoryUrl = (app_id: string, annotation_id: string) => `/apps/${app_id}/annotations/${annotation_id}/hit-logs`
export const getAnnotationHitHistory = (app_id: string, annotation_id: string) => {
  return request.get(getAnnotationHitHistoryUrl(app_id, annotation_id))
}