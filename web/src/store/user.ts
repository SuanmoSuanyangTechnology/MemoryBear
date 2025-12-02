import { create } from 'zustand'
import { clearAuthData } from '@/utils/auth';
import type { User } from '@/views/UserManagement/types'
import { getUsers, refreshToken, logout } from '@/api/user'
import { getWorkspaceStorageType } from '@/api/workspaces';

export interface LoginInfo {
  access_token: string;
  expires_at: string;
  refresh_expires_at: string;
  refresh_token: string;
  token_type: 'bearer'
}
export interface UserState {
  user: User;
  loginInfo: LoginInfo;
  storageType: string | null;
  updateLoginInfo: (values: LoginInfo) => void;
  getUserInfo: (flag?: boolean) => void;
  clearUserInfo: () => void;
  logout: () => void;
  getStorageType: () => void;
}
export const useUser = create<UserState>((set, get) => ({
  user: localStorage.getItem('user') ? JSON.parse(localStorage.getItem('user') || '{}') as User : {} as User,
  loginInfo: {} as LoginInfo,
  storageType: null,
  updateLoginInfo: (values: LoginInfo) => {
    localStorage.setItem('token', values.access_token);
    localStorage.setItem('refresh_token', values.refresh_token);
    set({ loginInfo: values });
  },
  getUserInfo: async (flag?: boolean) => {
    if (!localStorage.getItem('token')) {
      return
    }
    const localUser = JSON.parse(localStorage.getItem('user') || '{}') as User;
    if (localUser.id) {
      return
    }
    getUsers()
      .then((res) => {
        const response = res as User;
        set({ user: response })
        if (flag) {
          window.location.href = response.role && response.current_workspace_id ? '/#/' : '/#/space'
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
        localStorage.setItem('token', response.refresh_token);
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
  }
}))