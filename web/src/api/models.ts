/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:00:09 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:00:09 
 */
import { request } from '@/utils/request'
import type { MultiKeyForm, Query, KeyConfigModalForm, CompositeModelForm, CustomModelForm, Provider } from '@/views/ModelManagement/types'

// Model list
export const getModelListUrl = '/models'
export const getModelList = (data: Query) => {
  return request.get(getModelListUrl, data)
}
// Model type list
export const modelTypeUrl = '/models/type'
// Model provider list
export const modelProviderUrl = '/models/provider'
export const getModelProviderList = () => {
  return request.get(modelProviderUrl)
}
// New model list
export const getModelNewListUrl = '/models/new'
export const getModelNewList = (data: Query) => {
  return request.get(getModelNewListUrl, data)
}
// Get model information
export const getModelInfo = (model_id: string) => {
  return request.get(`/models/${model_id}`)
}
// Create composite model
export const addCompositeModel = (data: CompositeModelForm) => {
  return request.post('/models/composite', data)
}
// Update composite model
export const updateCompositeModel = (model_id: string, data: CompositeModelForm) => {
  return request.put(`/models/composite/${model_id}`, data)
}
// Delete composite model
export const deleteCompositeModel = (model_id: string) => {
  return request.delete(`/models/composite/${model_id}`)
}
// Get API keys for all matching models by provider
export const getProviderApiKeys = (data: { provider: Provider; is_active?: boolean; page?: number; pagesize?: number}) => {
  return request.get('/models/provider/apikeys', data)
}
// Create API keys for all matching models by provider
export const createProviderApiKeys = (data: KeyConfigModalForm, signal?: AbortSignal) => {
  return request.post('/models/provider/apikeys', data, { signal })
}
// Delete API keys for all matching models by provider
export const deleteProviderApiKeys = (api_key_id: string) => {
  return request.delete(`/models/provider/apikeys/${api_key_id}`)
}
// Get model API key
export const getModelApiKeys = (model_id: string) => {
  return request.get(`/models/${model_id}/apikeys`)
}
// Create model API key
export const addModelApiKey = (model_id: string, data: MultiKeyForm, signal?: AbortSignal) => {
  return request.post(`/models/${model_id}/apikeys`, data, { signal })
}
// Delete model API key
export const deleteModelApiKey = (model_id: string, api_key_id: string) => {
  return request.delete(`/models/${model_id}/apikeys/${api_key_id}`)
}
// Update model status
export const updateModelStatus = (model_id: string, data: { is_active: boolean; }) => {
  return request.put(`/models/${model_id}`, data)
}
// Model plaza list
export const getModelPlaza = (data: { search?: string; provider?: string; }) => {
  return request.get('/models/model_plaza', data)
}
// Add model to plaza
export const addModelPlaza = (model_base_id: string) => {
  return request.post(`/models/model_plaza/${model_base_id}/add`)
}
// Create custom model
export const addCustomModel = (data: CustomModelForm, signal?: AbortSignal) => {
  return request.post('/models', data, { signal })
}
// Update custom model
export const updateCustomModel = (model_base_id: string, data: CustomModelForm, signal?: AbortSignal) => {
  return request.put(`/models/${model_base_id}`, data, { signal })
}