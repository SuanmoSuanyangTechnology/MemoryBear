import { request } from '@/utils/request'
import type { SpaceModalData } from '@/views/SpaceManagement/types'
import type { ConfigModalData } from '@/views/UserMemory/types'

// 空间列表
export const getWorkspaces = () => {
  return request.get('/workspaces')
}
// 创建空间
export const createWorkspace = (values: SpaceModalData) => {
  return request.post('/workspaces', values)
}
// 切换空间
export const switchWorkspace = (workspaceId: string) => {
  return request.put(`/workspaces/${workspaceId}/switch`)
}
// 获取空间存储类型
export const getWorkspaceStorageType = () => {
  return request.get(`/workspaces/storage`)
}
// 获取空间模型配置
export const getWorkspaceModels = () => {
  return request.get(`/workspaces/workspace_models`)
}
// 更新空间模型配置
export const updateWorkspaceModels = (data: ConfigModalData) => {
  return request.put(`/workspaces/workspace_models`, data)
}
