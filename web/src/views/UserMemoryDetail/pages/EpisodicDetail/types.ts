export type EpisodicCategory = 'work_career' | 'learning_skills' | 'health_care' | 'finance_assets' | 'living_routine' | 'relationships' | 'leisure_experience' | 'other'

export interface EpisodicOverviewQuery {
  end_user_id: string
  category_l1?: EpisodicCategory | null
  keyword?: string | null
  sort?: 'scene_count_desc' | 'created_at_desc' | 'updated_at_desc'
  page?: number
  pagesize?: number
}

export interface EpisodicDetailQuery {
  end_user_id: string
  scene_community_id: string
  page?: number
  pagesize?: number
}

export interface EpisodicPage {
  page: number
  pagesize: number
  total: number
  hasnext: boolean
}

export interface EpisodicMemory {
  id: string
  topic_name: string | null
  topic_scope: string | null
  category_l1: string | null
  scene_count: number
  created_at: number | null
  updated_at: number | null
}

export interface EpisodicOverviewData {
  total: number
  items: EpisodicMemory[]
  page: EpisodicPage
}

export interface EpisodicScene {
  id: string
  topic_scope: string | null
  content: string
}

export interface EpisodicMemoryDetail extends EpisodicMemory {
  summary: string
  scenes: EpisodicScene[]
  page: EpisodicPage
}
