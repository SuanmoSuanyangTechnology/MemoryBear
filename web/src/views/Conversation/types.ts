/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:57:46 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-06 21:11:19
 */
/**
 * Type definitions for Conversation
 */

/**
 * Conversation history item
 */
export interface HistoryItem {
  /** Conversation ID */
  id: string;
  /** Application ID */
  app_id: string;
  /** Workspace ID */
  workspace_id: string;
  /** User ID */
  user_id: string | null;
  /** Conversation title */
  title: string;
  /** Conversation summary */
  summary?: string
  /** Whether conversation is draft */
  is_draft: boolean;
  /** Number of messages */
  message_count: number;
  /** Whether conversation is active */
  is_active: boolean;
  /** Creation timestamp */
  created_at: number;
  /** Update timestamp */
  updated_at: number;
}

/**
 * Query parameters for sending messages
 */
export interface QueryParams {
  /** Message content */
  message?: string;
  /** Whether to enable web search */
  web_search?: boolean;
  /** Whether to enable memory */
  memory?: boolean;
  /** Whether to use streaming response */
  stream: boolean;
  /** Current conversation ID */
  conversation_id?: string | null;
  files?: any[];
}

export interface UploadFileListModalRef {
  handleOpen: (fileList?: any[]) => void;
}