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

// Memory conversation
export const readService = (query: TestParams) => {
  return request.post('/memory/read_service', query)
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
export const getUserMemoryList = () => {
  return request.get(userMemoryListUrl)
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
  return request.get(`/memory-storage/analytics/memory_insight/report`, { end_user_id })
}
// User Memory - User summary
export const getUserSummary = (end_user_id: string) => {
  return request.get(`/memory-storage/analytics/user_summary`, { end_user_id })
}
// Memory classification
export const getNodeStatistics = (end_user_id: string) => {
  return request.get(`/memory-storage/analytics/node_statistics`, { end_user_id })
}
// Basic information
export const getEndUserProfile = (end_user_id: string) => {
  return request.get(`/memory-storage/read_end_user/profile`, { end_user_id })
}
export const updatedEndUserProfile = (values: EndUser) => {
  return request.post(`/memory-storage/updated_end_user/profile`, values)
}
// User Memory - Relationship network
export const getMemorySearchEdges = (end_user_id: string) => {
  return request.get(`/memory-storage/analytics/graph_data`, { end_user_id })
}
// User Memory - User interest distribution
export const getHotMemoryTagsByUser = (end_user_id: string) => {
  return request.get(`/memory/analytics/hot_memory_tags/by_user`, { end_user_id })
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
export const getRagContent = (end_user_id: string) => {
  return request.get(`/dashboard/rag_content`, { end_user_id, limit: 20 })
}
// Emotion distribution analysis
export const getWordCloud = (group_id: string) => {
  return request.post(`/memory/emotion-memory/wordcloud`, { group_id, limit: 20 })
}
// High-frequency emotion keywords
export const getEmotionTags = (group_id: string) => {
  return request.post(`/memory/emotion-memory/tags`, { group_id, limit: 20 })
}
// Emotion health index
export const getEmotionHealth = (group_id: string) => {
  return request.post(`/memory/emotion-memory/health`, { group_id, limit: 20 })
}
// Personalized suggestions
export const getEmotionSuggestions = (group_id: string) => {
  return request.post(`/memory/emotion-memory/suggestions`, { group_id, limit: 20 })
}
export const generateSuggestions = (end_user_id: string) => {
  return request.post(`/memory/emotion-memory/generate_suggestions`, { end_user_id })
}
export const analyticsRefresh = (end_user_id: string) => {
  return request.post('/memory-storage/analytics/generate_cache', { end_user_id })
}
// Forgetting stats
export const getForgetStats = (group_id: string) => {
  return request.get(`/memory/forget-memory/stats`, { group_id })
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
export const generateProfile = (end_user_id: string) => {
  return request.post(`/memory/implicit-memory/generate_profile`, { end_user_id })
}
// Short-term memory
export const getShortTerm = (end_user_id: string) => {
  return request.get(`/memory/short/short_term`, { end_user_id })
}
// Perceptual Memory - Visual memory
export const getPerceptualLastVisual = (end_user: string) => {
  return request.get(`/memory/perceptual/${end_user}/last_visual`)
}
// Perceptual Memory - Audio memory
export const getPerceptualLastListen = (end_user: string) => {
  return request.get(`/memory/perceptual/${end_user}/last_listen`)
}
// Perceptual Memory - Text memory
export const getPerceptualLastText = (end_user: string) => {
  return request.get(`/memory/perceptual/${end_user}/last_text`)
}
// Perceptual Memory - Perceptual memory timeline
export const getPerceptualTimeline = (end_user: string) => {
  return request.get(`/memory/perceptual/${end_user}/timeline`)
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
export const getExplicitMemory = (end_user_id: string) => {
  return request.post(`/memory/explicit-memory/overview`, { end_user_id })
}
export const getExplicitMemoryDetails = (data: { end_user_id: string, memory_id: string; }) => {
  return request.post(`/memory/explicit-memory/details`, data)
}
export const getConversations = (end_user: string) => {
  return request.get(`/memory/work/${end_user}/conversations`)
}
export const getConversationMessages = (end_user: string, conversation_id: string) => {
  return request.get(`/memory/work/${end_user}/messages`, { conversation_id })
}
export const getConversationDetail = (end_user: string, conversation_id: string) => {
  return request.get(`/memory/work/${end_user}/detail`, { conversation_id })
}
export const forgetTrigger = (data: { max_merge_batch_size: number; min_days_since_access: number; end_user_id: string;}) => {
  return request.post(`/memory/forget-memory/trigger`, data)
}
/*************** end User Memory APIs ******************************/

/****************** Memory Management APIs *******************************/
// Memory Management - Get all configurations
export const memoryConfigListUrl = '/memory-storage/read_all_config'
export const getMemoryConfigList = () => {
  return request.get(memoryConfigListUrl)
}
// Memory Management - Create configuration
export const createMemoryConfig = (values: MemoryFormData) => {
  return request.post('/memory-storage/create_config', values)
}
// Memory Management - Update configuration
export const updateMemoryConfig = (values: MemoryFormData) => {
  return request.post('/memory-storage/update_config', values)
}
// Memory Management - Delete configuration
export const deleteMemoryConfig = (config_id: number) => {
  return request.delete(`/memory-storage/delete_config?config_id=${config_id}`)
}
// Forgetting Engine - Get configuration
export const getMemoryForgetConfig = (config_id: number | string) => {
  return request.get('/memory/forget-memory/read_config', { config_id })
}
// Forgetting Engine - Update configuration
export const updateMemoryForgetConfig = (values: ForgetConfigForm) => {
  return request.post('/memory/forget-memory/update_config', values)
}
// Memory Extraction Engine - Get configuration
export const getMemoryExtractionConfig = (config_id: number | string) => {
  return request.get('/memory-storage/read_config_extracted', { config_id: config_id })
}
// Memory Extraction Engine - Update configuration
export const updateMemoryExtractionConfig = (values: ExtractionConfigForm) => {
  return request.post('/memory-storage/update_config_extracted', values)
}
// Memory Extraction Engine - Pilot run
export const pilotRunMemoryExtractionConfig = (values: { config_id: number | string; dialogue_text: string; }, onMessage?: (data: SSEMessage[]) => void) => {
  return handleSSE('/memory-storage/pilot_run', values, onMessage)
}
// Emotion Engine - Get configuration
export const getMemoryEmotionConfig = (config_id: number | string) => {
  return request.get('/memory/emotion/read_config', { config_id: config_id })
}
// Emotion Engine - Update configuration
export const updateMemoryEmotionConfig = (values: EmotionConfig) => {
  return request.post('/memory/emotion/updated_config', values)
}
// Reflection Engine - Get configuration
export const getMemoryReflectionConfig = (config_id: number | string) => {
  return request.get('/memory/reflection/configs', { config_id: config_id })
}
// Reflection Engine - Update configuration
export const updateMemoryReflectionConfig = (values: SelfReflectionEngineConfig) => {
  return request.post('/memory/reflection/save', values)
}
// Reflection Engine - Pilot run
export const pilotRunMemoryReflectionConfig = (values: { config_id: number | string; language_type: string; }) => {
  return request.get('/memory/reflection/run', values)
}

/*************** end Memory Management APIs ******************************/


/****************** API Parameters APIs *******************************/
export const getMemoryApi = () => {
  return request.get('/memory/docs/api')
}
/*************** end API Parameters APIs ******************************/