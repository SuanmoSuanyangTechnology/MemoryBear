/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:00:06 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-16 12:24:45
 */
import { request } from '@/utils/request'
import type { AxiosRequestConfig } from 'axios'
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
import type { ChatItem } from '@/components/Chat/types'

// Memory conversation
export const readService = (query: TestParams) => {
  return request.post('/memory/read/sync', query)
}
/****************** Memory Dashboard APIs *******************************/
// Memory Dashboard - Total memory count
export const getTotalMemoryCount = () => {
  return request.get(`/dashboard/total_memory_count`)
}
// Memory Dashboard - Knowledge base type distribution
export const getKbTypes = () => {
  return request.get(`/memory/stats/types`)
}
// Memory Dashboard - Hot memory tags
export const getHotMemoryTags = () => {
  return request.get(`/memory-storage/analytics/hot_memory_tags`)
}
// Memory Dashboard - Recent activity statistics
export const getRecentActivityStats = () => {
  return request.get(`/memory-storage/analytics/recent_activity_stats`)
}
// Memory Dashboard - Memory growth trend
export const getMemoryIncrement = (limit: number) => {
  return request.get(`/dashboard/memory_increment`, { limit })
}
// Memory Dashboard - API call trend
export const getApiTrend = () => {
  return request.get(`/dashboard/api_increment`)
}
// Memory Dashboard - Total data
export const getDashboardData = () => {
  return request.get(`/dashboard/dashboard_data`)
}
/*************** end Memory Dashboard APIs ******************************/


/****************** User Memory APIs *******************************/
export const userMemoryListUrl = '/dashboard/end_users'
export const getUserMemoryList = (query?: { keyword?: string }) => {
  return request.get(userMemoryListUrl, query)
}
// User Memory - Delete end user
export const deleteEndUser = (end_user_id: string) => {
  return request.delete(`/memory-storage/end-users/${end_user_id}`)
}
// User Memory - Total end users
export const getTotalEndUsers = () => {
  return request.get(`/dashboard/total_end_users`)
}
// User Memory - User profile
export const getUserProfile = (end_user_id: string) => {
  return request.get(`/memory/analytics/user_profile`, { end_user_id })
}

// User Memory - Memory insight
export const getMemoryInsightReport = (end_user_id: string) => {
  return request.get(`/memory/analytics/memory_insight`, { end_user_id })
}
// User Memory - User summary
export const getUserSummary = (end_user_id: string) => {
  return request.get(`/memory/analytics/user_summary`, { end_user_id })
}
// Memory classification
export const getNodeStatistics = (end_user_id: string) => {
  return request.get(`/memory/analytics/node_statistics`, { end_user_id })
}
// Get user alias and info
export const getEndUserInfo = (end_user_id: string) => {
  return request.get(`/end_user/info`, { end_user_id })
}
// Update user alias and info
export const updatedEndUserInfo = (values: EndUser) => {
  return request.post(`/end_user/info/update`, values)
}
// User Memory - Relationship network
export const getMemorySearchEdges = (end_user_id: string, config?: AxiosRequestConfig) => {
  return request.get(`/memory/analytics/graph_data`, { end_user_id }, config)
}
// User Memory - Delete memory node
export const deleteMemoryNode = (data: { end_user_id: string; element_id: string }) => {
  return request.post(`/memory-storage/node/delete`, data)
}
// User Memory - Community graph
export const getMemoryCommunityGraph = (end_user_id: string, config?: AxiosRequestConfig) => {
  return request.get(`/memory/analytics/community_graph`, { end_user_id }, config)
}
// User Memory - User interest distribution
export const getInterestDistributionByUser = (end_user_id: string) => {
  return request.get(`/memory/analytics/interest_distribution`, { end_user_id })
}
// User Memory - Total memory count
export const getTotalMemoryCountByUser = (end_user_id: string) => {
  return request.get(`/memory-storage/search`, { end_user_id })
}
// RAG User Memory - Total memory count
export const getTotalRagMemoryCountByUser = (end_user_id: string) => {
  return request.get(`/dashboard/current_user_rag_total_num`, { end_user_id })
}
// RAG User Memory - User summary
export const getChunkSummaryTag = (end_user_id: string) => {
  return request.get(`/dashboard/chunk_summary_tag`, { end_user_id })
}
// RAG User Memory - Memory insight
export const getChunkInsight = (end_user_id: string) => {
  return request.get(`/dashboard/chunk_insight`, { end_user_id })
}
// RAG User Memory - Storage content
export const getRagContentUrl = '/dashboard/rag_content'
export const getRagContent = (end_user_id: string, page = 1, pagesize = 20) => {
  return request.get(getRagContentUrl, { end_user_id, page, pagesize })
}
// Emotion distribution analysis
export const getWordCloud = (end_user_id: string) => {
  return request.post(`/memory/emotion-memory/wordcloud`, { end_user_id, limit: 20 })
}
// High-frequency emotion keywords
export const getEmotionTags = (end_user_id: string) => {
  return request.post(`/memory/emotion-memory/tags`, { end_user_id, limit: 20 })
}
// Emotion health index
export const getEmotionHealth = (end_user_id: string) => {
  return request.post(`/memory/emotion-memory/health`, { end_user_id })
}
// Personalized suggestions
export const getEmotionSuggestions = (end_user_id: string) => {
  return request.post(`/memory/emotion-memory/suggestions`, { end_user_id })
}
export const generateSuggestions = (end_user_id: string) => {
  return request.post(`/memory/emotion-memory/generate_suggestions`, { end_user_id })
}
export const analyticsRefresh = (end_user_id: string) => {
  return request.post('/memory/analytics/generate_cache', { end_user_id })
}
// 遗忘记忆配额表
export const getForgetMemoryQuota = (end_user_id: string | string) => {
  return request.get(`/memory/forget-memory/${end_user_id}/memory_quota`)
}
// 仅7日遗忘趋势
export const getForgetMemoryTrend = (end_user_id: string) => {
  return request.get(`/memory/forget-memory/${end_user_id}/forgetting_trend`)
}
// 遗忘候选
export const getForgetMemoryCandidatesUrl = (end_user_id: string) => `/memory/forget-memory/${end_user_id}/forgetting_candidates`
// 遗忘记录
export const getForgetMemoryLogsUrl = (end_user_id: string) => `/memory/forget-memory/${end_user_id}/forgotten_logs`
// 刷新（清除遗忘缓存）
export const refreshForgetMemoryCache = (end_user_id: string) => {
  return request.post(`/memory/forget-memory/${end_user_id}/refresh_cache`)
}
// Implicit Memory - Preferences
export const getImplicitPreferences = (end_user_id: string) => {
  return request.get(`/memory/implicit-memory/preferences/${end_user_id}`)
}
// Implicit Memory - Core traits
export const getImplicitPortrait = (end_user_id: string) => {
  return request.get(`/memory/implicit-memory/portrait/${end_user_id}`)
}
// Implicit Memory - Interest areas distribution
export const getImplicitInterestAreas = (end_user_id: string) => {
  return request.get(`/memory/implicit-memory/interest-areas/${end_user_id}`)
}
// Implicit Memory - User habits analysis
export const getImplicitHabits = (end_user_id: string) => {
  return request.get(`/memory/implicit-memory/habits/${end_user_id}`)
}
// Implicit Memory - Generate user portrait
export const generateProfile = (end_user_id: string) => {
  return request.post(`/memory/implicit-memory/generate_profile`, { end_user_id })
}
// Implicit Memory - Check if data exists
export const implicitCheckData = (end_user_id: string) => {
  return request.get(`/memory/implicit-memory/check-data/${end_user_id}`)
}
// Short-term memory
export const getShortTerm = (end_user_id: string) => {
  return request.get(`/memory/short/short_term`, { end_user_id })
}
// Perceptual Memory - Visual memory
export const getPerceptualLastVisual = (end_user_id: string) => {
  return request.get(`/memory/perceptual/${end_user_id}/last_visual`)
}
// Perceptual Memory - Audio memory
export const getPerceptualLastListen = (end_user_id: string) => {
  return request.get(`/memory/perceptual/${end_user_id}/last_listen`)
}
// Perceptual Memory - Text memory
export const getPerceptualLastText = (end_user_id: string) => {
  return request.get(`/memory/perceptual/${end_user_id}/last_text`)
}
// Perceptual Memory - Perceptual memory timeline
export const getPerceptualTimeline = (end_user_id: string) => {
  return request.get(`/memory/perceptual/${end_user_id}/timeline`)
}
// Episodic Memory - Overview
export const getEpisodicOverview = (data: { end_user_id: string; time_range: string; episodic_type: string; } ) => {
  return request.post(`/memory/episodic-memory/overview`, data)
}
export const getEpisodicDetail = (data: { end_user_id: string; summary_id: string; } ) => {
  return request.post(`/memory/episodic-memory/details`, data)
}
// Relationship evolution
export const getRelationshipEvolution = (data: { id: string; label: string; } ) => {
  return request.get(`/memory-storage/memory_space/relationship_evolution`, data)
}
// Shared memory timeline
export const getTimelineMemories = (data: { id: string; label: string; }) => {
  return request.get(`/memory-storage/memory_space/timeline_memories`, data)
}

// ExtractedEntity memory timeline
export const getEntityEventTimeline = (data: { id: string; type?: string; page?: number; pagesize?: number; }) => {
  return request.get('/memory-storage/memory_space/entity_timeline', data)
}
export const getExplicitMemory = (end_user_id: string) => {
  return request.post(`/memory/explicit-memory/overview`, { end_user_id })
}

export type EpisodicMemoryType = "conversation" | "project_work" | "learning" | "decision" | "important_event"
export interface EpisodicMemoryQuery {
  end_user_id?: string;
  page?: number;
  pagesize?: number;
  start_date?: number;
  end_date?: number;
  episodic_type?: EpisodicMemoryType;
}
// Explicit Memory - Episodic memory paginated query
export const getEpisodicMemory = (data: EpisodicMemoryQuery) => {
  return request.get(`/memory/explicit-memory/episodics`, data)
}
// Explicit Memory - Get user semantic memory list
export const getSemanticsMemory = (end_user_id: string) => {
  return request.get(`/memory/explicit-memory/semantics`, { end_user_id })
}
export const getExplicitMemoryDetails = (data: { end_user_id: string, memory_id: string; }) => {
  return request.post(`/memory/explicit-memory/details`, data)
}
// Work Memory Conversations
export const getConversations = (end_user_id: string, page = 1, pagesize = 20) => {
  return request.get(`/memory/work/${end_user_id}/conversations`, { page, pagesize })
}
// Work Memory Conversation Messages
export const getConversationMessages = (end_user_id: string, conversation_id: string) => {
  return request.get(`/memory/work/${end_user_id}/messages`, { conversation_id })
}
// Work Memory Conversation Detail
export const getConversationDetail = (end_user_id: string, conversation_id: string) => {
  return request.get(`/memory/work/${end_user_id}/detail`, { conversation_id })
}
// Work Memory API/MCP Data Sources
export const getApiMcpDataSources = (end_user_id: string) => {
  return request.get(`/memory/work/${end_user_id}/sources`)
}
// Work Memory API/MCP Messages Record
export const getApiMcpMessages = (end_user_id: string, data: { source: 'mcp' | 'service_api'; limit: number; }) => {
  return request.get(`/memory/work/${end_user_id}/source_messages`, data)
}
export const forgetTrigger = (data: { max_merge_batch_size: number; min_days_since_access: number; end_user_id: string;}) => {
  return request.post(`/memory/forget-memory/trigger`, data)
}
// RAG type - Refresh RAG user summary and memory insight
export const generateRagProfile = (end_user_id: string) => {
  return request.post(`/dashboard/generate_rag_profile`, { end_user_id })
}

// Reflect Log Statistics Overview
export const getReflectLogStats = (end_user_id: string) => {
  return request.get(`/memory/reflection/logs/stats`, { end_user_id })
}
// Get Reflect Log List (Pagination)
export const reflectLogListUrl = '/memory/reflection/logs'
export const getReflectLogs = (data: { end_user_id: string; page?: number; pagesize?: number; }) => {
  return request.get(reflectLogListUrl, data)
}
// Get Reflect Log Detail
export const getReflectLogDetail = (reflect_log_id: string) => {
  return request.get(`/memory/reflection/logs/${reflect_log_id}`)
}
/*************** end User Memory APIs ******************************/

/****************** Memory Management APIs *******************************/
// Memory Management - Get all configurations
export const memoryConfigListUrl = '/memory_config/read_all_config'
export const getMemoryConfigList = () => {
  return request.get(memoryConfigListUrl)
}
// Memory Management - Create configuration
export const createMemoryConfig = (values: MemoryFormData) => {
  return request.post('/memory_config/create_config', values)
}
// Memory Management - Update configuration
export const updateMemoryConfig = (values: MemoryFormData) => {
  return request.post('/memory_config/update_config', values)
}
// Memory Management - Delete configuration
export const deleteMemoryConfig = (config_id: string) => {
  return request.delete(`/memory_config/delete_config?config_id=${config_id}`)
}
// Memory Management - Set a configuration as the active online configuration
export const setActiveMemoryConfig = (config_id: string) => {
  return request.post('/memory_config/active_config', { config_id })
}
// Memory Management - Validate a configuration
export const validateMemoryConfig = () => {
  return request.get('/memory_config/validate_active_config')
}
// Forgetting Engine - Get configuration
export const getMemoryForgetConfig = (config_id: string | string) => {
  return request.get('/memory_config/read_config_forgetting', { config_id })
}
// Forgetting Engine - Update configuration
export const updateMemoryForgetConfig = (values: ForgetConfigForm) => {
  return request.post('/memory_config/update_config_forgetting', values)
}
// Memory Extraction Engine - Get configuration
export const getMemoryExtractionConfig = (config_id: string | string) => {
  return request.get('/memory_config/read_config_extracted', { config_id: config_id })
}
// Memory Extraction Engine - Update configuration
export const updateMemoryExtractionConfig = (values: ExtractionConfigForm) => {
  return request.post('/memory_config/update_config_extracted', values)
}
// Memory Extraction Engine - Pilot run
export const pilotRunMemoryExtractionConfig = (values: { config_id: string | string; messages: ChatItem[]; }, onMessage?: (data: SSEMessage[]) => void, onAbort?: (abort: () => void) => void) => {
  return handleSSE('/memory-storage/pilot_run', values, onMessage, undefined, onAbort)
}
// Emotion Engine - Get configuration
export const getMemoryEmotionConfig = (config_id: string | string) => {
  return request.get('/memory_config/read_config_emotion', { config_id: config_id })
}
// Emotion Engine - Update configuration
export const updateMemoryEmotionConfig = (values: EmotionConfig) => {
  return request.post('/memory_config/update_config_emotion', values)
}
// Reflection Engine - Get configuration
export const getMemoryReflectionConfig = (config_id: string | string) => {
  return request.get('/memory_config/read_config_reflection', { config_id: config_id })
}
// Reflection Engine - Update configuration
export const updateMemoryReflectionConfig = (values: SelfReflectionEngineConfig) => {
  return request.post('/memory_config/update_config_reflection', values)
}
// Reflection Engine - Pilot run
export const pilotRunMemoryReflectionConfig = (values: { config_id: string | string; language_type: string; }) => {
  return request.get('/memory/reflection/run', values)
}
// User Memory - Reflection logs
export const getMemoryReflectionLogsUrl = '/memory-storage/analytics/reflection_logs'
export const getMemoryReflectionLogs = (end_user_id: string) => {
  return request.get(getMemoryReflectionLogsUrl, { end_user_id })
}

/*************** end Memory Management APIs ******************************/


/****************** API Parameters APIs *******************************/
export const getMemoryApi = () => {
  return request.get('/memory/docs/api')
}
/*************** end API Parameters APIs ******************************/