/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:45:54 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-05 20:01:24
 */
import { type ReactNode } from 'react'
import { type ButtonProps } from 'antd'

/**
 * Chat message item interface
 */
export interface MemoryProfile {
  aliases_name?: string[];
  description?: string;
  core_facts?: string[];
  goals?: string[];
  interests?: string[];
  relations?: string[];
  beliefs_or_stances?: string[];
  anchors?: string[];
  events?: string[];
  traits?: string[];
}

export interface MemoryRecallItem {
  rank?: number;
  memory_type?: string;
  source?: string;
  score?: number;
  content?: string;
  relation?: string;
  target?: string;
  target_desc?: string;
  file?: {
    file_name?: string;
    file_path?: string;
    file_type?: string;
    perceptual_type?: number;
  };
}

export interface MemoryStageData {
  [key: string]: unknown;
  has_profile?: boolean;
  profile?: MemoryProfile;
  count?: number;
  questions?: string[];
  hit_count?: number;
  memory_count?: number;
  relation_count?: number;
  shown_count?: number;
  total_count?: number;
  duration_ms?: number;
  order?: string;
  items?: MemoryRecallItem[];
}

export interface MemoryStage {
  stage: string;
  status: string;
  data: MemoryStageData;
}

export interface MemoryToolCall {
  /** 仅用于流式阶段归属，后端持久化结构不会返回该字段 */
  step_id?: string;
  name: string;
  input: Record<string, unknown>;
  status: 'running' | 'completed' | 'failed';
  error?: string;
  stages: MemoryStage[];
}

export interface MemoryRetrieval {
  schema_version: number;
  tool_calls: MemoryToolCall[];
}

export type MemoryTraceEvent = 'tool_start' | 'memory_stage' | 'tool_end' | 'tool_error';

export interface MemoryTraceEventData {
  step_id?: string;
  name?: string;
  input?: string | Record<string, unknown>;
  stage?: string;
  status?: string;
  data?: MemoryStageData;
  error?: string;
  meta?: {
    tool_type?: string;
    [key: string]: unknown;
  };
}

export interface ChatItem {
  /** Message unique identifier */
  id?: string;
  /** Conversation ID */
  conversation_id?: string | null;
  /** Parent message ID */
  parent_message_id?: string | null;
  is_deleted?: boolean;
  like_count?: number;
  dislike_count?: number;
  report_count?: number;
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
  is_favorited?: boolean;
  meta_data?: {
    audio_url?: string | null;
    audio_status?: string;
    files?: any[];
    suggested_questions?: string[];
    citations?: CitationItem[];
    model?: string;
    usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
      total_tokens?: number;
    };
    reasoning_content?: string | null;
    memory_retrieval?: MemoryRetrieval;
    error?: string;
    waiting_human?: boolean;
    execution_id?: string;
    outputs?: {
      status?: string;
      content: string;
      node_id: string;
    }[]
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
    variant: ButtonProps['type'];
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
  /** Current input message (controlled; clearing it empties the input box) */
  message?: string;
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
  /** Whether the active stream should show memory recall details */
  showMemoryRecall?: boolean;
  /** Whether the last message is still receiving stream events */
  memoryRecallStreaming?: boolean;
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
  isAlwaysShowAssistantTools?: boolean;
  handleFeedback?: (feedbackType: 'like' | 'dislike', id?: string) => void;
  isEnded?: boolean;
  deleteMsg?: (vo: ChatItem) => void;
  reportMsg?: (vo: ChatItem) => void;
  regenerateMaxCount?: number;
  regenerateMessages?: (vo: ChatItem) => void;
  handleVersionChange?: (page: number, item: ChatItem) => void;
  handleInterventionActionClick?: (actionId: string, fieldValues: Record<string, string>, execution_id?: string, node_id?: string) => void;
  handleFavorite?: (id?: string) => void;
}