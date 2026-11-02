import { request } from '@/utils/request'
import type { SkillQuery, SkillPackageImportFormData, ImportSkill } from '@/views/Skills/types'

// Get skill list
export const getSkillListUrl = '/skills'
export const getSkillList = (data?: SkillQuery) => {
  return request.get(getSkillListUrl, data)
}
// Get skill details
export const getSkillDetail = (skill_id: string, data?: any) => {
  return request.get(`/skills/${skill_id}`, data)
}
// Delete skill
export const deleteSkill = (skill_id: string) => {
  return request.delete(`/skills/${skill_id}`)
}
// Import skill package
export const skillImport = (formData: SkillPackageImportFormData) => {
  const data = new FormData()
  console.log('formData', formData)
  Object.entries(formData).forEach(([key, value]) => {
    if (value !== undefined) data.append(key, value)
  })
  return request.uploadFile<ImportSkill>(`/skills/packages/import`, data)
}
// Export skill package
export const skillExport = (skill_id: string, fileName: string) => {
  return request.getDownloadFile(`/skills/${skill_id}/export`, fileName)
}