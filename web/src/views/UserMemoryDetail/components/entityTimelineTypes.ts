import type { Dayjs } from 'dayjs'

export type TimelineTab = 'all' | 'key_node' | 'statement' | 'memory_summary'

export interface ExtractedEntityMemory {
  type: string;
  category: string;
  title: string;
  text: string;
  created_at: string | number;
  timetype: 'recording';
}

export interface TypeStats {
  type: string;
  count: number;
}

export interface TimelineFormValues {
  sort?: string;
  timeRange?: (Dayjs | null)[] | null;
}

export interface Query extends TimelineFormValues {
  /** 与 end_user_id 二选一；Neo4j elementId */
  id?: string;
  end_user_id?: string;
  type?: TimelineTab;
  page?: number;
  pagesize?: number;
  from_ts?: number;
  to_ts?: number;
}

export interface TimelineResponse {
  items: ExtractedEntityMemory[];
  type_stats: TypeStats[];
  page: { total: number };
  total_count: number;
  filtered_counts: {
    key_node?: number;
    memory_summary?: number;
    total?: number;
  }
}

export const timelineSortOptions: Record<TimelineTab, string[]> = {
  all: ['time_asc', 'time_desc'],
  key_node: ['occurred_asc', 'occurred_desc'],
  statement: ['recorded_asc', 'recorded_desc'],
  memory_summary: ['recorded_asc', 'recorded_desc'],
}
