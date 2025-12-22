import { request } from '@/utils/request'
import type { ApplicationModalData } from '@/views/ApplicationManagement/types'
import type { Config } from '@/views/ApplicationConfig/types'
import { handleSSE, type SSEMessage } from '@/utils/stream'
import type { QueryParams } from '@/views/Conversation/types'
import type { WorkflowConfig } from '@/views/Workflow/types'

// 应用列表
export const getApplicationListUrl = '/apps'
export const getApplicationList = (data: Record<string, unknown>) => {
  return request.get(getApplicationListUrl, data)
}
// 获取应用配置
export const getApplicationConfig = (id: string) => {
  return request.get(`/apps/${id}/config`)
}
// 获取集群应用配置
export const getMultiAgentConfig = (id: string) => {
  return request.get(`/apps/${id}/multi-agent`)
}
// 获取 workflow应用配置
export const getWorkflowConfig = (id: string) => {
  return request.get(`/apps/${id}/workflow`)
}
// 应用详情
export const getApplication = (id: string) => {
  return request.get(`/apps/${id}`)
}
// 更新应用
export const updateApplication = (id: string, values: ApplicationModalData) => {
  return request.put(`/apps/${id}`, values)
}
// 创建应用
export const addApplication = (values: ApplicationModalData) => {
  return request.post('/apps', values)
}
// 保存Agent配置
export const saveAgentConfig = (app_id: string, values: Config) => {
  return request.put(`/apps/${app_id}/config`, values)
}
// 保存集群配置
export const saveMultiAgentConfig = (app_id: string, values: Config) => {
  return request.put(`/apps/${app_id}/multi-agent`, values)
}
// 保存workflow配置
export const saveWorkflowConfig = (app_id: string, values: WorkflowConfig) => {
  return request.put(`/apps/${app_id}/workflow`, values)
}
// 模型比对试运行
export const runCompare = (app_id: string, values: Record<string, unknown>, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE(`/apps/${app_id}/draft/run/compare`, values, onMessage)
}
export const draftRun = (app_id: string, values: Record<string, unknown>, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE(`/apps/${app_id}/draft/run`, values, onMessage)
}
// 删除应用
export const deleteApplication = (app_id: string) => {
  return request.delete(`/apps/${app_id}`)
}
// 发布版本列表
export const getReleaseList = (app_id: string) => {
  return request.get(`/apps/${app_id}/releases`)
}
// 发布版本
export const publishRelease = (app_id: string, values: Record<string, unknown>) => {
  return request.post(`/apps/${app_id}/publish`, values)
}
// 回滚版本
export const rollbackRelease = (app_id: string, version: string) => {
  return request.post(`/apps/${app_id}/rollback/${version}`)
}
// 发布版本分享
export const shareRelease = (app_id: string, release_id: string) => {
  return request.post(`/apps/${app_id}/releases/${release_id}/share`, {
    "is_enabled": true,
    "require_password": false,
    "allow_embed": true
  })
}
// 获取体验对话历史
export const getConversationHistory = (share_token: string, data: { page: number; pagesize: number }) => {
  return request.get(`/public/share/conversations`, data, {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem(`shareToken_${share_token}`)}`
    }
  })
}
// 发送体验对话
export const sendConversation = (values: QueryParams, onMessage: (data: SSEMessage[]) => void, shareToken: string) => {
  return handleSSE(`/public/share/chat`, values, onMessage, {
    headers: {
      'Authorization': `Bearer ${shareToken}`
    }
  })
}
// 获取体验会话详情
export const getConversationDetail = (share_token: string, conversation_id: string) => {
  return request.get(`/public/share/conversations/${conversation_id}`, {}, {
    headers: {
      'Authorization': `Bearer ${localStorage.getItem(`shareToken_${share_token}`)}`
    }
  })
}
// 获取体验对话token
export const getShareToken = (share_token: string, user_id: string) => {
  return request.post(`/public/share/${share_token}/token`, { user_id })
}
// 复制应用
export const copyApplication = (app_id: string, new_name: string) => {
  return request.post(`/apps/${app_id}/copy?new_name=${new_name}`)
}