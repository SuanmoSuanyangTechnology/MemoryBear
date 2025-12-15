import { request } from '@/utils/request'
import type { CreateModalData } from '@/views/UserManagement/types'
import { cookieUtils } from '@/utils/request'

// 用户信息
export const getUsers = () => {
  return request.get('/users')
}
// 用户列表
export const getUserListUrl = '/users/superusers'
// 登录
export const loginUrl = '/token'
export const login = (data: { email: string; password: string; invite?: string; username?: string }) => {
  return request.post(loginUrl, data)
}
// 刷新token
export const refreshTokenUrl = '/refresh'
export const refreshToken = () => {
  return request.post(refreshTokenUrl, { refresh_token: cookieUtils.get('refreshToken') })
}
// 重置密码
export const changePassword = (data: { user_id: string; new_password: string }) => {
  return request.put('/users/admin/change-password', data)
}
// 禁用用户
export const deleteUser = (user_id: string) => {
  return request.delete(`/users/${user_id}`)
}
// 启用用户
export const enableUser = (user_id: string) => {
  return request.post(`/users/${user_id}/activate`)
}
// 创建用户
export const addUser = (data: CreateModalData) => {
  return request.post('/users/superuser', data)
}
// 注销
export const logoutUrl = '/logout'
export const logout = () => {
  return request.post(logoutUrl)
}