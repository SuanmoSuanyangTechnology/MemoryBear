import { request } from '@/utils/request'
import type { Query, CustomToolItem, ExecuteData, MCPToolItem, InnerToolItem } from '@/views/ToolManagement/types'

// 工具列表
export const getTools = (data: Query) => {
  return request.get('/tools', data)
}
// 创建MCP工具
export const addTool = (values: MCPToolItem | CustomToolItem) => {
  return request.post('/tools', values)
}
// 更新工具
export const updateTool = (tool_id: string, data: MCPToolItem | InnerToolItem | CustomToolItem) => {
  return request.put(`/tools/${tool_id}`, data)
}
// 删除工具
export const deleteTool = (tool_id: string) => {
  return request.delete(`/tools/${tool_id}`)
}
// MCP 测试连接
export const testConnection = (tool_id: string) => {
  return request.post(`/tools/${tool_id}/test`)
}
// 工具测试
export const execute = (data: ExecuteData) => {
  return request.post(`/tools/execution/execute`, data)
}
export const parseSchema = (data: Record<string, any>) => {
  return request.post(`/tools/parse_schema`, data)

}