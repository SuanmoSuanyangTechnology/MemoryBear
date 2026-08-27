/**
 * Log / conversation record types.
 */
import type { ChatItem } from '@/components/Chat/types'

export interface LogItem {
  id: string;
  app_id: string;
  user_id: string;
  title: string;
  message_count: number;
  is_draft: boolean;
  created_at: number;
  updated_at: number;
  node_executions_map?: Record<string, ChatItem['subContent']>
  pending_intervention?: Record<string, { interventions: ChatItem['interventions'] }>


  execution_id: string;
  release_id: string;
  trigger_type: string;
  status: string;
  elapsed_time: number;
  error_message: null | string;
  started_at: number;
  completed_at: number;
}

export interface LogDetailModalRef {
  handleOpen: (vo: LogItem) => void;
}
