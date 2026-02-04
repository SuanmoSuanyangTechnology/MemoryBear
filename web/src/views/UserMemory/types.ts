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
  end_user: {
    id: string;
    app_id: string;
    other_id: string;
    other_name: string;
    other_address: string;
    created_at: string;
    updated_at: string;
  },
  memory_num: {
    total: number;
    counts: {
      dialogue: number;
      chunk: number;
      statement: number;
      entity: number;
    }
  },
  memory_config: {
    memory_config_id: string;
    memory_config_name: string;
  },
  type: string;
  name?: string;
}