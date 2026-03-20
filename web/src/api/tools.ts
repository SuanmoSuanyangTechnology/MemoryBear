import { request } from '@/utils/request'
import type { Query, MarketQuery, CustomToolItem, ExecuteData, MCPToolItem, InnerToolItem } from '@/views/ToolManagement/types'

// 工具列表
export const getTools = (data: Query) => {
  return request.get('/tools', data)
}
// 创建MCP工具
export const addTool = (values: MCPToolItem | CustomToolItem, config?: { signal?: AbortSignal }) => {
  return request.post('/tools', values, config)
}
// 更新工具
export const updateTool = (tool_id: string, data: MCPToolItem | InnerToolItem | CustomToolItem, config?: { signal?: AbortSignal }) => {
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
export const getToolDetail = (tool_id: string) => {
  return request.get(`/tools/${tool_id}`)
}
export const getToolMethods = (tool_id: string) => {
  return request.get(`/tools/${tool_id}/methods`)
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