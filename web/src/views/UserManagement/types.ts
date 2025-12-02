// 用户数据类型
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

// 用户表单数据类型
export interface CreateModalData {
  email: string;
  username: string;
  password: string;
}

// 用户表单数据类型
export interface CreateModalData {
  username: string;
  displayName: string;
  initialPassword?: string;
}
// 定义组件暴露的方法接口
export interface CreateModalRef {
  handleOpen: () => void;
}
export interface ResetPasswordModalRef {
  handleOpen: (user: User) => void;
}