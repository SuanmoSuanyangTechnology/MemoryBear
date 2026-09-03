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

export interface LogItem {
  type?: string
  title?: string
  stage?: string
  status?: string
  query?: string
  reason?: string
  result?: string
  summary?: string
  input?: unknown
  data?: unknown
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

export interface StreamUpdate {
  answer?: string
  appendAnswer?: boolean
  sessionId?: string
  log?: LogItem
  completed?: boolean
}

export interface ChatState {
  data: ChatItem[]
  logs: LogItem[]
  loading: boolean
  sessionId?: string
}

export type LoosePayload = StreamPayload & AnyObject
