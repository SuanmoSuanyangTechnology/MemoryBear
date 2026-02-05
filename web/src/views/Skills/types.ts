/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:49:35 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-05 10:49:35 
 */
/**
 * Skill Form Data Structure
 * Defines the data structure for creating and updating skills
 */
export interface SkillFormData {
  /** Skill name */
  name: string;
  /** Skill description */
  description: string;
  /** Array of tools associated with this skill */
  tools: Array<{
    /** Tool identifier */
    tool_id: string;
  }>;
  /** Skill configuration settings */
  config: {
    /** Keywords for skill matching and discovery */
    keywords: string[];
    /** Whether the skill is enabled */
    enabled: boolean;
  };
  /** AI prompt/instructions for the skill */
  prompt: string;
  /** Whether the skill is active */
  is_active: boolean;
  /** Whether the skill is publicly accessible */
  is_public: boolean;
}

/**
 * Complete Skill Data Structure
 * Extends SkillFormData with system-generated fields
 */
export interface Skill extends SkillFormData {
  /** Unique skill identifier */
  id: string;
  /** Tenant/organization identifier */
  tenant_id: string;
  /** Creation timestamp */
  created_at: number;
  /** Last update timestamp */
  updated_at: number;
}
