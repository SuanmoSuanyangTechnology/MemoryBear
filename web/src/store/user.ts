/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:33:54 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:33:54 
 */
/**
 * User Store
 * 
 * Manages user authentication and profile with:
 * - User information storage
 * - Login/logout functionality
 * - Token management (access & refresh)
 * - Workspace storage type
 * - Navigation guards for workspace access
 * 
 * @store
 */

import { create } from 'zustand'
import { clearAuthData } from '@/utils/auth';
import type { User } from '@/views/UserManagement/types'
import { getUsers, refreshToken, logout } from '@/api/user'
import { getWorkspaceStorageType } from '@/api/workspaces';
import { cookieUtils } from '@/utils/request'

/** Login information interface */
export interface LoginInfo {
  access_token: string;
  expires_at: string;
  refresh_expires_at: string;
  refresh_token: string;
  token_type: 'bearer'
}

/** User state interface */
export interface UserState {
  /** Current user information */
  user: User;
  /** Login token information */
  loginInfo: LoginInfo;
  /** Workspace storage type */
  storageType: string | null;
  /** Update login information */
  updateLoginInfo: (values: LoginInfo) => void;
  /** Get user information */
  getUserInfo: (flag?: boolean) => void;
  /** Clear user information */
  clearUserInfo: () => void;
  /** Logout user */
  logout: () => void;
  /** Get workspace storage type */
  getStorageType: () => void;
  /** Check and redirect if workspace not set */
  checkJump: () => void;
}

/** Pages that don't require workspace */
export const whitePage = [
  '/conversation',
  '/login',
  '/invite-register'
]

/** User store */
export const useUser = create<UserState>((set, get) => ({
  user: localStorage.getItem('user') ? JSON.parse(localStorage.getItem('user') || '{}') as User : {} as User,
  loginInfo: {} as LoginInfo,
  storageType: null,
  updateLoginInfo: (values: LoginInfo) => {
    cookieUtils.set('authToken', values.access_token);
    cookieUtils.set('refreshToken', values.refresh_token);
    set({ loginInfo: values });
  },
  getUserInfo: async (flag?: boolean) => {
    if (!cookieUtils.get('authToken')) {
      return
    }
    const { checkJump } = get()
    const localUser = JSON.parse(localStorage.getItem('user') || '{}') as User;
    if (localUser.id) {
      checkJump()
      return
    }
    getUsers()
      .then((res) => {
        const response = res as User;
        set({ user: response })
        if (flag) {
          window.location.href = response.role && response.current_workspace_id ? '/#/' : '/#/index'
        }
        localStorage.setItem('user', JSON.stringify(response))
      })
      .catch((err) => {
        console.error('Failed to fetch user info:', err)
      })
  },
  clearUserInfo: () => {
    set({ user: {} as User })
    clearAuthData();
  },
  logout: () => {
    logout()
      .then(() => {
        const { clearUserInfo } = get()
        clearUserInfo()
        window.location.href = '/#/login'
      })
      .catch((err) => {
        console.error('Failed to logout:', err)
      })
  },
  refreshToken: () => {
    refreshToken()
      .then((res) => {
        const response = res as { refresh_token: string }
        cookieUtils.set('authToken', response.refresh_token);
      })
      .catch((err) => {
        console.error('Failed to refresh token:', err)
      })
  },
  getStorageType: () => {
    getWorkspaceStorageType()
      .then((res) => {
        const response = res as { storage_type: string };
        set({ storageType: response.storage_type || 'neo4j' });
      })
      .catch(() => {
        console.error('Failed to load storage type');
      })
  },
  checkJump: () => {
    const localUser = JSON.parse(localStorage.getItem('user') || '{}') as User;
    const hash = window.location.hash;

    /** Redirect to index if user has no workspace and not on whitelist page */
    if (localUser.id && (!localUser.current_workspace_id || localUser.current_workspace_id === '') && !whitePage.find(vo => hash.includes(vo))) {
      console.log('whitePage', whitePage.find(vo => hash.includes(vo)))
      window.location.href = '/#/index'
    }
  },
}))
