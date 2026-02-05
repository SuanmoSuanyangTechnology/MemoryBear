/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:28:44 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-05 10:28:44 
 */
import { request } from '@/utils/request'
import type { SkillFormData } from '@/views/Skills/types'

// Get skill list
export const getSkillListUrl = '/skills'
export const getSkillList = (data?: any) => {
  return request.get(getSkillListUrl, data)
}
// Get skill details
export const getSkillDetail = (skill_id: string, data?: any) => {
  return request.get(`/skills/${skill_id}`, data)
}
// Create skill
export const createSkill = (data: SkillFormData) => {
  return request.post('/skills', data)
}
// Update skill
export const updateSkill = (skill_id: string, data: SkillFormData) => {
  return request.put(`/skills/${skill_id}`, data)
}
// Delete skill
export const deleteSkill = (skill_id: string) => {
  return request.delete(`/skills/${skill_id}`)
}