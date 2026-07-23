/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:53:36 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:53:36 
 */
/**
 * User memory data structure
 */
export interface Data {
  end_user_id: string;
  end_user: {
    id: string;
    other_name: string;
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
}