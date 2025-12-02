export interface LoginForm {
  email: string;
  password: string;
  confirmPassword: string;
  username: string;
}
export interface ValidateToken {
  workspace_name: string;
  workspace_id: string;
  email: string;
  role: string;
  is_expired: boolean;
  is_valid: boolean;
}