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
  llm: string;
  embedding: string;
  rerank: string;
}
/**
 * Space config component ref interface
 */
export interface SpaceConfigRef {
  handleOpen: () => void;
}