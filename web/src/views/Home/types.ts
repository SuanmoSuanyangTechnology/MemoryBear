import type { activityKinds } from './constants'

export type ActivityKind = typeof activityKinds[number]

export interface ActivityDay {
  date: string;
  counts: Record<ActivityKind, number>;
  total?: number;
  categories?: ActivityCategories;
}

export interface ActivityStatisticsResponse {
  total_activities: number;
  active_days: number;
  daily_average: number;
  peak_daily_activities: number;
  involved_users: number;
  category_totals: Record<ActivityKind, number>;
  daily_activities: Array<{
    date: string;
    timestamp: number;
    total_activities: number;
  }>;
}

export type ActivityCategories = Record<ActivityKind, { count: number; percentage: number }>

export interface ActivityCompositionResponse {
  summary: {
    total_activities: number;
    categories: ActivityCategories;
  };
  daily_activities: Array<{
    date: string;
    timestamp: number;
    total_activities: number;
    categories: ActivityCategories;
  }>;
}

export interface ActivityQuery {
  start_date: string;
  end_date: string;
}

export interface ActivityResponse {
  days: ActivityDay[];
  user_count: number;
  simulated: boolean;
}

export interface DashboardData {
  total_memory: number;
  total_app: number;
  total_knowledge: number;
  total_api_call: number;
  total_memory_change: number;
  total_app_change: number;
  total_knowledge_change: number;
  total_api_call_change: number;
}
export interface WorkspaceStatistics {
  statistics: Array<{
    type:
      | 'PERCEPTUAL_MEMORY'
      | 'WORKING_MEMORY'
      | 'SHORT_TERM_MEMORY'
      | 'EXPLICIT_MEMORY'
      | 'IMPLICIT_MEMORY'
      | 'EMOTIONAL_MEMORY'
      | 'EPISODIC_MEMORY'
      | 'FORGET_MEMORY';
    count: number;
    percentage: number;
    change: number;
  }>;
  total_count: number;
  total_count_change: number;
  total_users: number;
  memory_update: number;
  memory_update_change: number;
  generated_at: number;
}

export interface DashboardDataResponse {
  storage_type: 'neo4j' | 'rag';
  neo4j_data: DashboardData;
  rag_data?: DashboardData
  workspace_statistics: WorkspaceStatistics;
  memory_update: number;
  memory_update_change: number;
}