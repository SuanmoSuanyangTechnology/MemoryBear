/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:52:53 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 15:52:53 
 */
import type { Dayjs } from 'dayjs'
import { maskApiKeys } from '@/utils/apiKeyReplacer'

/**
 * API Key data structure
 */
export interface ApiKey {
  /** Unique identifier */
  id: string;
  /** API key name */
  name: string;
  /** Optional description */
  description?: string;
  /** API key type */
  type: 'agent' | 'multi_agent' | 'workflow' | 'service';
  /** Permission scopes: 'memory' | 'rag' | 'app' */
  scopes?: string[];

  /** The actual API key string */
  api_key: string;
  /** Whether the key is active */
  is_active: boolean;
  /** Whether the key has expired */
  is_expired: boolean;
  /** Creation timestamp */
  created_at: number;
  /** Expiration timestamp or Dayjs object */
  expires_at?: number | Dayjs;
  /** Memory engine permission flag */
  memory?: boolean;
  /** RAG/Knowledge base permission flag */
  rag?: boolean;

  /** Last update timestamp */
  updated_at: string;
  /** Queries per second limit */
  qps_limit?: number;
  /** Daily request limit */
  daily_request_limit?: number;

  /** Rate limit */
  rate_limit?: number;
  /** Total number of requests made */
  total_requests: number;
  /** Quota used */
  quota_used: number;
  /** Quota limit */
  quota_limit: number;
}

/**
 * Ref methods exposed by API Key modal components
 */
export interface ApiKeyModalRef {
  /**
   * Open the modal
   * @param apiKey - Optional API key data for edit mode
   */
  handleOpen: (apiKey?: ApiKey) => void;
  /** Close the modal */
  handleClose: () => void;
}

/**
 * Get masked API key for display
 * @param apiKey - The API key to mask
 * @returns Masked API key string
 */
export const getMaskedApiKey = (apiKey: string): string => {
  return maskApiKeys(apiKey)
}