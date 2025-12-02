import { request } from '@/utils/request'
import type { ModelFormData } from '@/views/ModelManagement/types'

// 模型列表
export const getModelListUrl = '/models'
export const getModelList = (data: { type: string; pagesize: number; page: number; }) => {
  return request.get(getModelListUrl, data)
}
// 创建模型
export const addModel = (data: ModelFormData) => {
  return request.post('/models', data)
}
// 更新模型
export const updateModel = (apiKeyId: string, data: ModelFormData) => {
  return request.put(`/models/apikeys/${apiKeyId}`, data)
}
// 模型类型列表
export const modelTypeUrl = '/models/type'
// 模型供应商列表
export const modelProviderUrl = '/models/provider'
export const getModelProviderList = () => {
  return request.get(modelProviderUrl)
}