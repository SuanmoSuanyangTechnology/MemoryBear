/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:53:36 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:53:36 
 */
export type MemoryType = 'long' | 'short';
/**
 * User memory data structure
 */

export interface Data {
  end_user_id: string;
  end_user: {
    id: string;
    other_name: string;
    label: MemoryType;
    other_id: string;
    write_time: number;
    expire_time: number;
    identity_features: string;
  },
  memory_num: {
    total: number;
    active_count: number;
    memory_limit: number;
  },
  memory_config: {
    memory_config_id: string;
    memory_config_name: string;
  },
  tags: string[];
}

export interface Query {
  keyword?: string;
  label?: 'long' | 'short';
}