import type { AnyObject } from 'antd/es/_util/type'
import type { Data } from '@/views/UserMemory/types'
import type { ChatItem } from '@/components/Chat/types'

export interface TestParams {
  end_user_id: string
  message: string
  search_switch: string
  web_search?: boolean
  memory?: boolean
  conversation_id?: string
  session_id?: string
}

export interface CommunitySearchParams {
  end_user_id: string
  message: string
  search_switch: '6'
  session_id?: string
}

export type CommunityStageStatus = 'running' | 'completed' | 'skipped' | 'failed'

export type CommunityEmptyReason =
  | 'no_community_hit'
  | 'no_scene_hit_in_communities'
  | 'no_valid_evidence'

export interface CommunitySearchItem {
  id: string
  node_type: 'SceneCommunity'
  topic_scope: string
  rank: number
  score: number
}

export interface CommunitySceneItem {
  id: string
  node_type: 'SceneSummary'
  topic_scope: string
  community_id: string
  rank: number
  score: number
}

export interface CommunityEvidenceItem {
  id: string
  node_type: 'SceneSummary'
  community_id: string
  title: string
  content: string
  score: number
  rank: number
  evidence_no: number
}

/** Stage snapshots may be incomplete; data may be an empty object for failed or skipped stages. */
export interface CommunityStageData<T> {
  items?: T[]
  empty_reason?: CommunityEmptyReason | null
}

export interface CommunityStageDataMap {
  community_searched: CommunityStageData<CommunitySearchItem>
  community_scenes_searched: CommunityStageData<CommunitySceneItem>
  community_evidence_packed: CommunityStageData<CommunityEvidenceItem>
}

export type CommunityStage = keyof CommunityStageDataMap

/** Discriminate each stage's item structure by its stage key. */
export type CommunityStageLog = {
  [Stage in CommunityStage]: LogItem<CommunityStageDataMap[Stage]> & {
    request_id: string
    stage: Stage
    status: CommunityStageStatus
    data: CommunityStageDataMap[Stage]
  }
}[CommunityStage]

export interface CommunityTraceItem {
  id: string
  community_id: string
  evidence_no: number
}

export interface CommunityRetrievalTrace {
  mode: 'community'
  status: 'completed'
  empty_reason: CommunityEmptyReason | null
  items: CommunityTraceItem[]
}

export interface CommunityStartPayload {
  request_id: string
  session_id: string
  search_switch: '6'
  mode: 'community'
  backend: 'neo4j'
  limit: number
}

export interface CommunityTracePayload {
  request_id: string
  trace: CommunityRetrievalTrace
}

export interface CommunityMessagePayload {
  request_id: string
  content: string
}

export interface CommunityEndPayload {
  request_id: string
  session_id: string
  intermediate_outputs: []
}

export interface CommunityErrorPayload {
  request_id: string
  code: number
  message: string
}

/** Map SSE event names to their parsed business payloads. */
export interface CommunityStreamEventMap {
  start: CommunityStartPayload
  memory_stage: CommunityStageLog
  retrieval_trace: CommunityTracePayload
  message: CommunityMessagePayload
  end: CommunityEndPayload
  error: CommunityErrorPayload
}

export type CommunityStreamEvent = {
  [Event in keyof CommunityStreamEventMap]: {
    event: Event
    data: CommunityStreamEventMap[Event]
  }
}[keyof CommunityStreamEventMap]

export interface LogItem<TData = unknown> {
  type?: string
  title?: string
  stage?: string
  status?: string
  query?: string
  reason?: string
  result?: string
  summary?: string
  input?: unknown
  data?: TData
  [key: string]: unknown
}

export type MemoryItem = Data

export type StreamPayload = Partial<{
  answer: string
  content: string
  session_id: string
  conversation_id: string
  intermediate_outputs: LogItem[]
}> & {
  log?: LogItem
  [key: string]: unknown
}

export interface StreamErrorPayload {
  request_id?: string
  code?: string | number
  message?: string
}

export interface StreamUpdate {
  answer?: string
  appendAnswer?: boolean
  sessionId?: string
  log?: LogItem
  error?: StreamErrorPayload
  completed?: boolean
}

export interface ChatState {
  data: ChatItem[]
  logs: LogItem[]
  loading: boolean
  sessionId?: string
}

export type LoosePayload = StreamPayload & AnyObject
