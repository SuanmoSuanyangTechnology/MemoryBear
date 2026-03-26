/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 13:59:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-23 18:05:43
 */
import { request, API_PREFIX } from '@/utils/request'

// Upload file，file storage has expiration period
export const fileUploadUrlWithoutApiPrefix = '/storage/files'
export const fileUploadUrl = `${API_PREFIX}${fileUploadUrlWithoutApiPrefix}`
export const fileUpload = (formData?: unknown) => {
  return request.uploadFile(fileUploadUrlWithoutApiPrefix, formData)
}

// Get file access URL (no token required)
export const getFileUrl = (file_id: string) => `/storage/files/${file_id}/url`
export const getFileLink = (fileId: string, data: { permanent?: boolean } = { permanent: true }) => {
  return request.get(getFileUrl(fileId), data)
}

// Get file internally
export const getInternalFileUrl = (file_id: string) => `/storage/files/${file_id}`
export const getInternalFile = (fileId: string) => {
  return request.get(getInternalFileUrl(fileId))
}

// Delete file
export const deleteFileUrl = (file_id: string) => `/storage/files/${file_id}`
export const deleteFile = (fileId: string) => {
  return request.delete(deleteFileUrl(fileId))
}

export const shareFileUploadUrlWithoutApiPrefix = `/storage/share/files`
export const shareFileUploadUrl = `${API_PREFIX}${shareFileUploadUrlWithoutApiPrefix}`

// Get file info
export const getFileInfoByUrl = (url: string) => {
  return request.get('/storage/files/info-by-url', {url})
}
// Get file status
export const getFileStatusById = (file_id: string) => {
  return request.get(`/storage/files/${file_id}/status`)
}