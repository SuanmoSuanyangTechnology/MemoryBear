import { request } from '@/utils/request'
import type {
  MemoryFormData,
} from '@/views/MemoryManagement/types'
import type {
  ConfigForm as ForgetConfigForm
} from '@/views/ForgettingEngine/types'
import type {
  ConfigForm as ExtractionConfigForm
} from '@/views/MemoryExtractionEngine/types'
import type {
  ConfigForm as EmotionConfig
} from '@/views/EmotionEngine/types'
import type {
  ConfigForm as SelfReflectionEngineConfig
} from '@/views/SelfReflectionEngine/types'
import type { TestParams } from '@/views/MemoryConversation'
import type { EndUser } from '@/views/UserMemoryDetail/types'
import { handleSSE, type SSEMessage } from '@/utils/stream'

// 记忆对话
export const readService = (query: TestParams) => {
  return request.post('/memory/read_service', query)
}
/****************** 记忆看板 相关接口 *******************************/
// 记忆看板-记忆总量
export const getTotalMemoryCount = () => {
  return request.get(`/dashboard/total_memory_count`)
}
// 记忆看板-知识库类型分布
export const getKbTypes = () => {
  return request.get(`/memory/stats/types`)
}
// 记忆看板-热门记忆标签
export const getHotMemoryTags = () => {
  return request.get(`/memory-storage/analytics/hot_memory_tags`)
}
// 记忆看板-最近活动统计
export const getRecentActivityStats = () => {
  return request.get(`/memory-storage/analytics/recent_activity_stats`)
}
// 记忆看板-记忆增长趋势
export const getMemoryIncrement = (limit: number) => {
  return request.get(`/dashboard/memory_increment`, { limit })
}
// 记忆看板-API调用趋势
export const getApiTrend = () => {
  return request.get(`/dashboard/api_increment`)
}
// 记忆看板-总数据
export const getDashboardData = () => {
  return request.get(`/dashboard/dashboard_data`)
}
/*************** end 记忆看板 相关接口 ******************************/


/****************** 用户记忆 相关接口 *******************************/
export const userMemoryListUrl = '/dashboard/end_users'
export const getUserMemoryList = () => {
  return request.get(userMemoryListUrl)
}
// 用户记忆-用户记忆总量
export const getTotalEndUsers = () => {
  return request.get(`/dashboard/total_end_users`)
}
// 用户记忆-用户详情
export const getUserProfile = (end_user_id: string) => {
  return request.get(`/memory/analytics/user_profile`, { end_user_id })
}

// 用户记忆-记忆洞察
export const getMemoryInsightReport = (end_user_id: string) => {
  return request.get(`/memory-storage/analytics/memory_insight/report`, { end_user_id })
}
// 用户记忆-用户摘要
export const getUserSummary = (end_user_id: string) => {
  return request.get(`/memory-storage/analytics/user_summary`, { end_user_id })
}
// 记忆分类
export const getNodeStatistics = (end_user_id: string) => {
  return request.get(`/memory-storage/analytics/node_statistics`, { end_user_id })
}
// 基本信息
export const getEndUserProfile = (end_user_id: string) => {
  return request.get(`/memory-storage/read_end_user/profile`, { end_user_id })
}
export const updatedEndUserProfile = (values: EndUser) => {
  return request.post(`/memory-storage/updated_end_user/profile`, values)
}
// 用户记忆-关系网络
export const getMemorySearchEdges = (end_user_id: string) => {
  return request.get(`/memory-storage/analytics/graph_data`, { end_user_id })
}
// 用户记忆-用户兴趣分布
export const getHotMemoryTagsByUser = (end_user_id: string) => {
  return request.get(`/memory/analytics/hot_memory_tags/by_user`, { end_user_id })
}
// 用户记忆-记忆总量
export const getTotalMemoryCountByUser = (end_user_id: string) => {
  return request.get(`/memory-storage/search`, { end_user_id })
}
// RAG 用户记忆-记忆总量
export const getTotalRagMemoryCountByUser = (end_user_id: string) => {
  return request.get(`/dashboard/current_user_rag_total_num`, { end_user_id })
}
// RAG 用户记忆-用户摘要
export const getChunkSummaryTag = (end_user_id: string) => {
  return request.get(`/dashboard/chunk_summary_tag`, { end_user_id })
}
// RAG 用户记忆-记忆洞察
export const getChunkInsight = (end_user_id: string) => {
  return request.get(`/dashboard/chunk_insight`, { end_user_id })
}
// RAG 用户记忆-存储内容
export const getRagContent = (end_user_id: string) => {
  return request.get(`/dashboard/rag_content`, { end_user_id, limit: 20 })
}
// 情感分布分析
export const getWordCloud = (group_id: string) => {
  return request.post(`/memory/emotion/wordcloud`, { group_id, limit: 20 })
}
// 高频情绪关键词
export const getEmotionTags = (group_id: string) => {
  return request.post(`/memory/emotion/tags`, { group_id, limit: 20 })
}
// 情绪健康指数
export const getEmotionHealth = (group_id: string) => {
  return request.post(`/memory/emotion/health`, { group_id, limit: 20 })
}
// 个性化建议
export const getEmotionSuggestions = (group_id: string) => {
  return request.post(`/memory/emotion/suggestions`, { group_id, limit: 20 })
}
export const analyticsRefresh = (end_user_id: string) => {
  return request.post('/memory-storage/analytics/generate_cache', { end_user_id })
}

/*************** end 用户记忆 相关接口 ******************************/

/****************** 记忆管理 相关接口 *******************************/
// 记忆管理-获取所有配置
export const memoryConfigListUrl = '/memory-storage/read_all_config'
export const getMemoryConfigList = () => {
  return request.get(memoryConfigListUrl)
}
// 记忆管理-创建配置
export const createMemoryConfig = (values: MemoryFormData) => {
  return request.post('/memory-storage/create_config', values)
}
// 记忆管理-更新配置
export const updateMemoryConfig = (values: MemoryFormData) => {
  return request.post('/memory-storage/update_config', values)
}
// 记忆管理-删除配置
export const deleteMemoryConfig = (config_id: number) => {
  return request.delete(`/memory-storage/delete_config?config_id=${config_id}`)
}
// 遗忘引擎-获取配置
export const getMemoryForgetConfig = (config_id: number | string) => {
  return request.get('/memory-storage/read_config_forget', { config_id })
}
// 遗忘引擎-更新配置
export const updateMemoryForgetConfig = (values: ForgetConfigForm) => {
  return request.post('/memory-storage/update_config_forget', values)
}
// 记忆萃取引擎-获取配置
export const getMemoryExtractionConfig = (config_id: number | string) => {
  return request.get('/memory-storage/read_config_extracted', { config_id: config_id })
}
// 记忆萃取引擎-更新配置
export const updateMemoryExtractionConfig = (values: ExtractionConfigForm) => {
  return request.post('/memory-storage/update_config_extracted', values)
}
// 记忆萃取引擎-试运行
export const pilotRunMemoryExtractionConfig = (values: { config_id: number | string; dialogue_text: string; }, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE('/memory-storage/pilot_run', values, onMessage)
}
// 情绪引擎-获取配置
export const getMemoryEmotionConfig = (config_id: number | string) => {
  return request.get('/memory/emotion/read_config', { config_id: config_id })
}
// 情绪引擎-更新配置
export const updateMemoryEmotionConfig = (values: EmotionConfig) => {
  return request.post('/memory/emotion/updated_config', values)
}
// 反思引擎-获取配置
export const getMemoryReflectionConfig = (config_id: number | string) => {
  return request.get('/memory/reflection/configs', { config_id: config_id })
}
// 反思引擎-更新配置
export const updateMemoryReflectionConfig = (values: SelfReflectionEngineConfig) => {
  return request.post('/memory/reflection/save', values)
}
// 反思引擎-试运行
export const pilotRunMemoryReflectionConfig = (values: { config_id: number | string; language_type: string; }) => {
  return request.get('/memory/reflection/run', values)
}

/*************** end 记忆管理 相关接口 ******************************/


/****************** API参数 相关接口 *******************************/
export const getMemoryApi = () => {
  return request.get('/memory/docs/api')
}
/*************** end API参数 相关接口 ******************************/