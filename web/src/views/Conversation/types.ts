export interface HistoryItem {
  id: string;
  app_id: string;
  workspace_id: string;
  user_id: string | null;
  title: string;
  summary?: string
  is_draft: boolean;
  message_count: number;
  is_active: boolean;
  created_at: number;
  updated_at: number;
}

export interface QueryParams {
  message?: string;
  web_search?: boolean;
  memory?: boolean;
  stream: boolean;
  conversation_id?: string | null;
}