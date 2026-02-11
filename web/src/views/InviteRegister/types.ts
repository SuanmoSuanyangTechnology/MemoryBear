/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:37:20 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:37:20 
 */
/**
 * Type definitions for Invite Register
 */

/**
 * Registration form data
 */
export interface LoginForm {
  /** User email address */
  email: string;
  /** User password */
  password: string;
  /** Password confirmation */
  confirmPassword: string;
  /** User display name */
  username: string;
}

/**
 * Invite token validation response
 */
export interface ValidateToken {
  /** Workspace name */
  workspace_name: string;
  /** Workspace ID */
  workspace_id: string;
  /** Invited user email */
  email: string;
  /** User role in workspace */
  role: string;
  /** Whether token is expired */
  is_expired: boolean;
  /** Whether token is valid */
  is_valid: boolean;
}