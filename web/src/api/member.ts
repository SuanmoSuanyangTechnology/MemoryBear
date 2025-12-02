import { request } from '@/utils/request'

// 成员列表
export const memberListUrl = '/workspaces/members'
// 邀请成员
export const inviteMember = (values: { email: string }) => {
  return request.post(`/workspaces/invites`, values)
}
// 删除成员
export const deleteMember = (id: string) => {
  return request.delete(`/workspaces/members/${id}`)
}
// 更新成员
export const updateMember = (values: { id: string, role: string }) => {
  return request.put(`/workspaces/members`, [values])
}
// 验证邀请token
export const validateInviteToken = (token: string) => {
  return request.get(`/workspaces/invites/validate/${token}`)
}
