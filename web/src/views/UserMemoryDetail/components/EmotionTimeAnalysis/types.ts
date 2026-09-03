import type { Dayjs } from 'dayjs'

export type ViewType = 'overview' | 'timeline'

export interface EmotionStat {
  type: string
  displayName: string
  count: number
  ratio: number
  color: string
}

export interface DayGroup {
  key: string
  date: Dayjs
  dialogueCount: number
  dataQuality: EmotionDailyItem['data_quality']
  summary?: string
  stats: EmotionStat[]
}

export interface EmotionOverviewQuery {
  end_user_id: string
}

export interface EmotionTimelineQuery {
  end_user_id: string
  page?: number
  pagesize?: number
  sort?: 'asc' | 'desc'
  start_date?: string
  end_date?: string
}

export interface EmotionDailyItem {
  date: string
  dialogue_count: number
  data_quality: 'normal' | 'too_few' | 'one_day' | 'no_data'
  emotions: Array<{
    type: string
    display_name: string
    count: number
    percentage: number
  }>
  summary?: string;
}

export interface EmotionConclusion {
  type: 'low_sample' | 'dominant_shift' | 'concentrated' | 'scattered' | 'stable'
  title: string
  message: string
  from_emotion?: string
  to_emotion?: string
}

export interface EmotionOverviewResponse {
  data_quality: 'normal' | 'too_few' | 'one_day' | 'no_data'
  summary: { dialogue_count: number; emotion_type_count: number }
  conclusion: EmotionConclusion | null
  items: EmotionDailyItem[]
}

export interface EmotionTimelineResponse {
  page: { page: number; pagesize: number; total: number; hasnext: boolean }
  gaps: Array<{
    from_date: string
    to_date: string
    days: number
    message: string
  }>
  items: EmotionDailyItem[]
}
