/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:06 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:48:06 
 */

/**
 * Space configuration data
 */
export interface SpaceConfigData {
  /** Config package mode: default recommended package or custom per-model selection */
  is_default_config?: boolean | string;
  llm?: string;
  embedding?: string;
  rerank?: string;
  vision?: string;
  audio?: string;
  video?: string;
}
/**
 * Space config component ref interface
 */
export interface SpaceConfigRef {
  handleOpen: () => void;
}