import { request } from '@/utils/request'
import type { Query, MarketQuery, CustomToolItem, ExecuteData, MCPToolItem, InnerToolItem } from '@/views/ToolManagement/types'
import type { ToolDetail, ToolMethod, ToolUpdate, ToolMethodsBatch, ToolStatistics } from '@/views/ToolManagement/toolContract'

// 工具列表
export const getToolsUrl = '/tools'
export const getTools = (data: Query) => {
  return request.get(getToolsUrl, data)
}
// 创建MCP工具
export const addTool = (values: MCPToolItem | CustomToolItem, config?: { signal?: AbortSignal }) => {
  return request.post('/tools', values, config)
}
// 更新工具
export const updateTool = (tool_id: string, data: MCPToolItem | InnerToolItem | CustomToolItem | ToolUpdate, config?: { signal?: AbortSignal }) => {
  return request.put(`/tools/${tool_id}`, data, config)
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
export const getToolDetail = (tool_id: string, query?: { include_external_status?: boolean }) => {
  return request.get<ToolDetail>(`/tools/${tool_id}`, query)
}
export const getToolMethods = (tool_id: string) => {
  return request.get<ToolMethod[]>(`/tools/${tool_id}/methods`)
}

export const getToolMethodsBatch = (tool_ids: string[]) => {
  return request.post<ToolMethodsBatch>('/tools/methods/batch', { tool_ids })
}
export const getToolStatistics = () => {
  return request.get<ToolStatistics>('/tools/statistics')
}
export const getToolAuthTypes = () => request.get('/tools/auth/types')
export const getToolTypes = () => request.get('/tools/enums/tool_types')
export const updateToolActive = (tool_id: string, is_active: boolean) => {
  return request.patch(`/tools/${tool_id}/active`, { is_active })
}

// MCP市场列表
export const getMarketTools = (data?: Record<string, any>) => {
  return request.get('/mcp_markets/mcp_markets', data)
}
// 市场配置创建
export const createMarketConfig = (values: {
  mcp_market_id: string;
  token: string;
  status: number;
}) => {
  return request.post('/mcp_market_configs/mcp_market_config', values)
}
// 市场配置更新
export const updateMarketConfig = (values: {
  mcp_market_config_id: string;
  token: string;
  status: number;
}) => {
  return request.put(`/mcp_market_configs/${values.mcp_market_config_id}`, values)
}
// 市场根据id获取配置
export const getMarketConfig = (mcp_market_id: string) => {
  return request.get(`/mcp_market_configs/mcp_market_id/${mcp_market_id}`)
}
// 市场MCP列表
export const getMarketMCPs = (data: MarketQuery) => {
  return request.get('/mcp_market_configs/mcp_servers', data)
}
// 根据配置ID serverId 获取MCP服务详情
export const getMarketMCPDetail = (data:{
  mcp_market_config_id: string;
  server_id: string;
}) => {
  return request.get(`/mcp_market_configs/mcp_server`,data)
}
// 市场已激活MCP列表
export const getMarketMCPsActivated = (data: MarketQuery) => {
  return request.get('/mcp_market_configs/operational_mcp_servers', data)
}