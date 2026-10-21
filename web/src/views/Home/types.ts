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