import type { ButtonProps } from 'antd'

import type { ChatItem } from '@/components/Chat/types'
import type { LogItem } from '@/views/ApplicationConfig/types'

export type Data = LogItem & {
  messages: Array<ChatItem | ChatItem[]>;
}

/**
 * Typed payload carried by a workflow SSE message
 */
export type StreamEventData = {
  content: string;
  message_id?: string;
  user_message_id?: string;
  execution_id?: string;
  conversation_id: string | null;
  cycle_id: string;
  cycle_idx: number;
  node_id: string;
  node_name?: string;
  node_type?: string;
  process?: any;
  input?: any;
  output?: any;
  elapsed_time?: string;
  error?: any;
  state: Record<string, any>;
  status?: 'completed' | 'failed' | 'running' | 'waiting_human';
  citations?: {
    document_id: string;
    file_name: string;
    knowledge_id: string;
    score: string;
  }[];
  rendered_content?: string;
  form_fields?: {
    id: string;
    default_value?: string;
  }[];
  actions?: {
    id: string;
    label: string;
    variant: ButtonProps['type'];
  }[];
  timeout_at?: number;
  agent_log?: Record<string, any>;
};

/** Node display metadata resolved from the graph */
export type NodeContext = {
  name?: any;
  icon?: any;
  type?: any;
};
