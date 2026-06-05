/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:45:54 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-05 20:01:24
 */
import { type ReactNode } from 'react'

/**
 * Chat message item interface
 */
export interface ChatItem {
  /** Message unique identifier */
  id?: string;
  /** Conversation ID */
  conversation_id?: string | null;
  /** Message role: user or assistant */
  role?: 'user' | 'assistant';
  /** Message content */
  content?: string | null;
  /** Creation time */
  created_at?: number | string;
  status?: string;
  subContent?: Record<string, any>[];
  error?: string;
  feedback_type?: 'like' | 'dislike' | null;
  meta_data?: {
    audio_url?: string | null;
    audio_status?: string;
    files?: any[];
    suggested_questions?: string[];
    citations?: CitationItem[];
    reasoning_content?: string;
    error?: string;
    waiting_human?: boolean;
    execution_id?: string;
  },
  version?: number;
  is_current?: boolean;
  is_hidden_refresh?: boolean;
  interventions?: Intervention[]
}

export interface CitationItem {
  document_id: string;
  file_name: string;
  knowledge_id: string;
  score: string;
  download_url?: string;
}
export interface Intervention {
  execution_id?: string;
  node_id?: string;
  node_name?: string;
  rendered_content?: string;
  form_fields?: {
    id: string;
    variable_ref?: any;
    default_value?: string;
  }[];
  actions?: {
    id: string;
    label: string;
    variant: string;
  }[];
  timeout_at?: number;

  resolved_action_id?: string;
  resolved_form_data?: Record<string, string>;
  
  resolved_at?: string;
  resolved_kind?: string;
}
/**
 * Chat component main props interface
 */
export interface ChatProps extends Omit<ChatContentProps, 'onSend'> {
  /** Input content change callback */
  onChange: (message: string) => void;
  /** Send message callback */
  onSend: () => void;
  /** Loading state */
  loading: boolean;
  /** Content area custom class name */
  contentClassName?: string;
  /** Child component content */
  children?: ReactNode;
  /** Attachment list */
  fileList?: any[];
  /** Attachment update */
  fileChange?: (fileList: any[]) => void;
  className?: string;
  conversationId?: string | null;
  readOnly?: boolean;
}

/**
 * Chat input component props interface
 */
export interface ChatInputProps {
  /** Current input message */
  message?: string;
  /** Input content change callback */
  onChange?: (message: string) => void;
  /** Send message callback */
  onSend: (message?: string) => void;
  /** Loading state */
  loading: boolean;
  /** Child component content */
  children?: ReactNode;
  /** Attachment list */
  fileList?: any[];
  /** Attachment update */
  fileChange?: (fileList: any[]) => void;
  className?: string;
}

/**
 * Chat content area component props interface
 */
export interface ChatContentProps {
  /** Custom class name */
  classNames?: string | Record<string, boolean>;
  contentClassNames?: string | Record<string, boolean>;
  /** Chat data list */
  data: Array<ChatItem | ChatItem[]>;
  /** Streaming loading state */
  streamLoading: boolean;
  /** Empty state display content */
  empty?: ReactNode;
  /** Label position: top or bottom */
  labelPosition?: 'top' | 'bottom';
  /** Label format function */
  labelFormat: (item: ChatItem) => any;
  errorDesc?: string;
  renderRuntime?: (item: ChatItem, index: number) => ReactNode;
  /** Send message callback */
  onSend?: (msg: string) => void;
  userIcon?: ReactNode;
  assistantIcon?: ReactNode;
  isSupportTools?: boolean;
  handleFeedback?: (feedbackType: 'like' | 'dislike', id?: string) => void;
  isEnded?: boolean;
  deleteMsg?: (vo: ChatItem) => void;
  reportMsg?: (vo: ChatItem) => void;
  regenerateMessages?: (vo: ChatItem) => void;
  handleVersionChange?: (page: number, item: ChatItem) => void;
  handleInterventionActionClick?: (actionId: string, fieldValues: Record<string, string>, execution_id?: string, node_id?: string) => void;
}