/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 13:59:41 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 13:59:41 
 */
import { request } from '@/utils/request'
import type { ApiKey } from '@/views/ApiKeyManagement/types'

// API Key list
export const getApiKeyListUrl = '/apikeys'
export const getApiKeyList = (data: Record<string, unknown>) => {
  return request.get(getApiKeyListUrl, data)
}

// API Key details
export const getApiKey = (id: string) => {
  return request.get(`/apikeys/${id}`)
}

// Create API Key
export const createApiKey = (values: ApiKey) => {
  return request.post('/apikeys', values)
}

// Update API Key
export const updateApiKey = (id: string, values: ApiKey) => {
  return request.put(`/apikeys/${id}`, values)
}

// Delete API Key
export const deleteApiKey = (id: string) => {
  return request.delete(`/apikeys/${id}`)
}

// Usage statistics
export const getApiKeyStats = (app_key_id: string) => {
  return request.get(`/apikeys/${app_key_id}/stats`)
}