import { request } from '@/utils/request'
import type { ApiKey } from '@/views/ApiKeyManagement/types'

// API Key列表
export const getApiKeyListUrl = '/apikeys'
export const getApiKeyList = (data: Record<string, unknown>) => {
  return request.get(getApiKeyListUrl, data)
}

// API Key详情
export const getApiKey = (id: string) => {
  return request.get(`/apikeys/${id}`)
}

// 创建API Key
export const createApiKey = (values: ApiKey) => {
  return request.post('/apikeys', values)
}

// 更新API Key
export const updateApiKey = (id: string, values: ApiKey) => {
  return request.put(`/apikeys/${id}`, values)
}

// 删除 API Key
export const deleteApiKey = (id: string) => {
  return request.delete(`/apikeys/${id}`)
}

// 使用统计
export const getApiKeyStats = (app_key_id: string) => {
  return request.get(`/apikeys/${app_key_id}/stats`)
}