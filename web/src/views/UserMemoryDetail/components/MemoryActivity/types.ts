export type ActivityType = 'write' | 'read' | 'engine';

export type ActivityFilter = 'all' | ActivityType;

export type ActivityDateGroup = 'today' | 'yesterday' | 'earlier';

export type MemoryType = 'conversation' | 'project_work' | 'learning' | 'decision' | 'important_event';

export type EngineType = 'EXTRACTION' | 'CROSS_MODAL' | 'EMOTION';
export interface MemoryActivityProps {
  className?: string
}


export interface ActivityRecord {
  id: string
  memory_id: string
  memory_type?: MemoryType
  engine_type?: EngineType;
  name: string
  content: string
  occurred_at: number
}

export interface ActivityQuery {
  end_user_id: string
}
