/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:43:09 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-05 10:43:09 
 */
import type { Skill } from '@/views/Skills/types'

/**
 * Skill Configuration Form Data Structure
 * Used to manage skill configuration settings in the application
 */
export interface SkillConfigForm {
  /** Whether skill configuration is enabled */
  enabled?: boolean;
  /** Whether all skills are accessible */
  all_skills?: boolean;
  /** Array of selected skill IDs or full skill objects */
  skill_ids?: Skill[] | string[];
}

/**
 * Skill Binding Mode Types
 * Defines different strategies for skill assignment
 */
export type SkillMode = 'staticBinding' | 'dynamicBinding' | 'mixedMode'

/**
 * Skill Configuration Modal Reference Interface
 * Provides methods to control the skill configuration modal
 */
export interface SkillConfigModalRef {
  /** Opens the modal with optional initial data */
  handleOpen: (data: SkillConfigForm) => void;
}

/**
 * Skill Global Configuration Modal Reference Interface
 * Provides methods to control the global skill configuration modal
 */
export interface SkillGlobalConfigModalRef {
  /** Opens the global configuration modal */
  handleOpen: () => void;
}

/**
 * Skill Selection Modal Reference Interface
 * Provides methods to control the skill selection modal
 */
export interface SkillModalRef {
  /** Opens the modal with optional skill configuration array */
  handleOpen: (config?: SkillConfigForm[]) => void;
}