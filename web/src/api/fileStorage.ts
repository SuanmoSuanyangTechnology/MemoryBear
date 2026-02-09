/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 13:59:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-09 16:24:05
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