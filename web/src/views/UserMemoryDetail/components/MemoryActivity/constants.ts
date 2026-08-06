import {
  BookOutlined,
  BulbOutlined,
  DatabaseOutlined,
  EyeOutlined,
  LaptopOutlined,
  MessageOutlined,
  StarOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'

import {
  getMemoryActivityEngineUrl,
  getMemoryActivityReadUrl,
  getMemoryActivityUrl,
  getMemoryActivityWrittenUrl,
} from '@/api/memory'
import { isPrivateAvailable } from '@/utils/private'
import type { ActivityDateGroup, ActivityFilter, ActivityType, MemoryType, SearchMode } from './types'

export const activityDateGroups: ActivityDateGroup[] = ['today', 'yesterday', 'earlier']

export const activityDateLabelKeys: Record<ActivityDateGroup, string> = {
  today: 'activityDateToday',
  yesterday: 'activityDateYesterday',
  earlier: 'activityDateEarlier',
}
export const filterKeys: ActivityFilter[] = (
  [
    'all',
    'engine',
    'read',
    'write',
  ] satisfies ActivityFilter[]
).filter((key) => (!isPrivateAvailable && key !== 'engine') || isPrivateAvailable)

export const filterLabelKeys: Record<ActivityFilter, string> = {
  all: 'activityFilterAll',
  engine: 'activityFilterEngine',
  read: 'activityFilterRead',
  write: 'activityFilterWrite',
}

export const activityUrls: Record<ActivityFilter, string> = {
  all: getMemoryActivityUrl,
  engine: getMemoryActivityEngineUrl,
  read: getMemoryActivityReadUrl,
  write: getMemoryActivityWrittenUrl,
}

export const activityIcons: Record<Exclude<ActivityType, 'all'>, typeof DatabaseOutlined> = {
  write: DatabaseOutlined,
  read: EyeOutlined,
  engine: ThunderboltOutlined,
}

export const memoryTypeIcons: Record<MemoryType, typeof DatabaseOutlined> = {
  conversation: MessageOutlined,
  project_work: LaptopOutlined,
  learning: BookOutlined,
  decision: BulbOutlined,
  important_event: StarOutlined,
}

export const searchModeIcons: Record<SearchMode, string> = {
  deep: "rb:bg-[url('@/assets/images/conversation/deepThinking.svg')]",
  normal: "rb:bg-[url('@/assets/images/conversation/normalReply.svg')]",
  quick: "rb:bg-[url('@/assets/images/conversation/quickReply.svg')]",
  express: "rb:bg-[url('@/assets/images/conversation/quickReplyPlus.svg')]",
}
