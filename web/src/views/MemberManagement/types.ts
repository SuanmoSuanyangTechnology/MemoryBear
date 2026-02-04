/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:42:00 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:42:00 
 */
/**
 * Type definitions for Member Management
 */

/**
 * Member data structure
 */
export interface Member {
  /** Member ID */
  id: string;
  /** Member username */
  username: string;
  /** Member account (email) */
  account: string;
  /** Member role */
  role: string;
  /** Last login timestamp */
  last_login_at: string | number;
}

/**
 * Member invitation/edit form data
 */
export interface MemberModalData {
  /** Member email address */
  email: string;
  /** Member role */
  role: string;
}

/**
 * Member modal ref interface
 */
export interface MemberModalRef {
  /** Open modal with optional member data for editing */
  handleOpen: (user?: Member | null) => void;
}