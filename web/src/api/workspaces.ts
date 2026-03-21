/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:00:26 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:00:26 
 */
import { request } from '@/utils/request'
import type { SpaceModalData } from '@/views/SpaceManagement/types'
import type { SpaceConfigData } from '@/views/SpaceConfig/types'

// Workspace list
export const getWorkspaces = () => {
  return request.get('/workspaces')
}
// Create workspace
export const createWorkspace = (values: SpaceModalData) => {
  return request.post('/workspaces', values)
}
// Switch workspace
export const switchWorkspace = (workspaceId: string) => {
  return request.put(`/workspaces/${workspaceId}/switch`)
}
// Get workspace storage type
export const getWorkspaceStorageType = () => {
  return request.get(`/workspaces/storage`)
}
// Get workspace model config
export const getWorkspaceModels = () => {
  return request.get(`/workspaces/workspace_models`)
}
// Update workspace model config
export const updateWorkspaceModels = (data: SpaceConfigData) => {
  return request.put(`/workspaces/workspace_models`, data)
}
