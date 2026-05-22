/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:45:54 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-22 14:11:15
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
  },
  version?: number;
  is_current?: boolean;
  is_hidden_refresh?: boolean;
}

export interface CitationItem {
  document_id: string;
  file_name: string;
  knowledge_id: string;
  score: string;
  download_url?: string;
}

/**
 * Chat component main props interface
 */
export interface ChatProps {
  /** Empty state display content */
  empty?: ReactNode;
  /** Chat data list */
  data: Array<ChatItem | ChatItem[]>;
  /** Input content change callback */
  onChange: (message: string) => void;
  /** Send message callback */
  onSend: () => void;
  /** Streaming loading state */
  streamLoading?: boolean;
  /** Loading state */
  loading: boolean;
  /** Content area custom class name */
  contentClassName?: string;
  /** Child component content */
  children?: ReactNode;
  /** Label format function */
  labelFormat: (item: ChatItem) => any;
  errorDesc?: string;
  /** Attachment list */
  fileList?: any[];
  /** Attachment update */
  fileChange?: (fileList: any[]) => void;
  className?: string;
  renderRuntime?: (item: ChatItem, index: number) => ReactNode;
  conversationId?: string | null;

  userIcon?: ReactNode;
  assistantIcon?: ReactNode;
  isSupportTools?: boolean;
  handleFeedback?: (feedbackType: 'like' | 'dislike', id?: string) => void;

  isEnded?: boolean;
  readOnly?: boolean;
  deleteMsg?: (vo: ChatItem) => void;
  reportMsg?: (vo: ChatItem) => void;
  regenerateMessages?: (vo: ChatItem) => void;
  handleVersionChange?: (page: number, item: ChatItem) => void;
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
}