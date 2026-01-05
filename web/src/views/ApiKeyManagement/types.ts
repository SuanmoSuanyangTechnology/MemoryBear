import type { Dayjs } from 'dayjs'
import { maskApiKeys } from '@/utils/apiKeyReplacer'

export interface ApiKey {
  id: string;
  name: string;
  description?: string;
  type: 'agent' | 'multi_agent' | 'workflow' | 'service';
  scopes?: string[]; // 'memory' | 'rag' | 'app'

  api_key: string;
  is_active: boolean;
  is_expired: boolean;
  created_at: number;
  expires_at?: number | Dayjs;
  memory?: boolean;
  rag?: boolean;


  updated_at: string;
  qps_limit?: number;
  daily_request_limit?: number;

  rate_limit?: number;
  total_requests: number;
  quota_used: number;
  quota_limit: number;
}

export interface ApiKeyModalRef {
  handleOpen: (apiKey?: ApiKey) => void;
  handleClose: () => void;
}

/**
 * 获取掩码后的API密钥
 */
export const getMaskedApiKey = (apiKey: string): string => {
  return maskApiKeys(apiKey)
}