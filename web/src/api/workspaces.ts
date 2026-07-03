/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:00:26 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-03 21:16:31
 */
import { request } from '@/utils/request'
import type { SpaceModalData } from '@/views/SpaceManagement/types'
import type { SpaceConfigData } from '@/views/SpaceConfig/types'

// Workspace list
export const getWorkspacesUrl = '/workspaces'
export const getWorkspaces = (data?: { include_current?: boolean }) => {
  return request.get(getWorkspacesUrl, data)
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
// Get current default configured model
export const getDefaultWorkspaceModel = () => {
  return request.get(`/workspaces/default_models`)
}
// Get custom workspace models options
export const getCustomWorkspaceModels = () => {
  return request.get(`/workspaces/model_options`)
}
// Validate workspace models config
export const validateWorkspaceModels = (data: SpaceConfigData) => {
  return request.post(`/workspaces/workspace_models/validate`, data)
}
// Update workspace models config and validate model availability
export const updateWorkspaceModelsAndValidate = (data: SpaceConfigData) => {
  return request.put(`/workspaces/workspace_models`, data)
}