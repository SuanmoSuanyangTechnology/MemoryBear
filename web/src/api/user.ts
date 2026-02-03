/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:00:23 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:00:23 
 */
import { request } from '@/utils/request'
import type { CreateModalData } from '@/views/UserManagement/types'
import { cookieUtils } from '@/utils/request'

// User info
export const getUsers = () => {
  return request.get('/users')
}
// User list
export const getUserListUrl = '/users/superusers'
// Login
export const loginUrl = '/token'
export const login = (data: { email: string; password: string; invite?: string; username?: string }) => {
  return request.post(loginUrl, data)
}
// Refresh token
export const refreshTokenUrl = '/refresh'
export const refreshToken = () => {
  return request.post(refreshTokenUrl, { refresh_token: cookieUtils.get('refreshToken') })
}
// Reset password
export const changePassword = (data: { user_id: string; new_password: string }) => {
  return request.put('/users/admin/change-password', data)
}
// Disable user
export const deleteUser = (user_id: string) => {
  return request.delete(`/users/${user_id}`)
}
// Enable user
export const enableUser = (user_id: string) => {
  return request.post(`/users/${user_id}/activate`)
}
// Create user
export const addUser = (data: CreateModalData) => {
  return request.post('/users/superuser', data)
}
// Logout
export const logoutUrl = '/logout'
export const logout = () => {
  return request.post(logoutUrl)
}