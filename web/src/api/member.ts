/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:00:01 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:00:01 
 */
import { request } from '@/utils/request'

// Member list
export const memberListUrl = '/workspaces/members'
// Invite member
export const inviteMember = (values: { email: string }) => {
  return request.post(`/workspaces/invites`, values)
}
// Delete member
export const deleteMember = (id: string) => {
  return request.delete(`/workspaces/members/${id}`)
}
// Update member
export const updateMember = (values: { id: string, role: string }) => {
  return request.put(`/workspaces/members`, [values])
}
// Validate invite token
export const validateInviteToken = (token: string) => {
  return request.get(`/workspaces/invites/validate/${token}`)
}
