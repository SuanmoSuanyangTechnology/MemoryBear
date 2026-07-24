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
}

export interface LogDetailModalRef {
  handleOpen: (vo: LogItem) => void;
}
