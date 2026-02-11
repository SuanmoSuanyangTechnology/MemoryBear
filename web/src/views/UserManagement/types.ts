/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:50:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-03 17:51:17
 */
/**
 * User data type
 */
export interface User {
  username: string;
  email: string;
  id: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string | number;
  last_login_at: string | number;
  current_workspace_id?: string;
  current_workspace_name?: string;
  role: 'member' | 'manager' | null;
  [key: string]: unknown;
}

/**
 * User form data type
 */
export interface CreateModalData {
  email: string;
  username: string;
  password: string;
}

/**
 * User form data type (duplicate definition)
 */
export interface CreateModalData {
  username: string;
  displayName: string;
  initialPassword?: string;
}
/**
 * Component exposed methods interface
 */
export interface CreateModalRef {
  handleOpen: () => void;
}
/**
 * Reset password modal ref interface
 */
export interface ResetPasswordModalRef {
  handleOpen: (user: User) => void;
}