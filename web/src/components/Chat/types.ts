/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:45:54 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-06 21:05:09 
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
  files?: any[];
}

/**
 * Chat component main props interface
 */
export interface ChatProps {
  /** Empty state display content */
  empty?: ReactNode;
  /** Chat data list */
  data: ChatItem[];
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
  data: ChatItem[];
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
}