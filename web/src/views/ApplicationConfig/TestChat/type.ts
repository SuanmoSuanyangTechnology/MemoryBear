import type { ButtonProps } from 'antd'
import type { Application } from '@/views/ApplicationManagement/types'
import type { Config } from '../types';
import type { WorkflowConfig } from '@/views/Workflow/types';

export interface TestChatProps {
  application?: Application | null;
  config: Config | WorkflowConfig | null
}

/** Typed payload carried by a draft-run SSE message */
export interface NodeData {
  execution_id?: string;
  message_id?: string;
  user_message_id?: string;
  content: string;
  conversation_id: string | null;
  cycle_id: string;
  cycle_idx: number;
  node_id: string;
  node_name?: string;
  node_type?: string;
  input?: any;
  output?: any;
  process?: any;
  elapsed_time?: string;
  error?: any;
  state: Record<string, any>;
  status?: 'completed' | 'failed';
  audio_url?: string;
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
  }[]
  actions?: {
    id: string;
    label: string;
    variant: ButtonProps['type'];
  }[];
  timeout_at?: number;
  agent_log?: any;
}