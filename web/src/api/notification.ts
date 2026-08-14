import { request } from '@/utils/request'
import { handleSSE, type SSEMessage } from '@/utils/stream'
import type { NotificationMessageTab } from '@/store/notification/types'

const encodePathSegment = (value: string) => encodeURIComponent(value)

// Notification list
export const getNotificationListUrl = '/notifications'
export const getNotifications = (data?: {
  is_read?: boolean
  tab?: NotificationMessageTab
  page?: number
  pagesize?: number
}) => {
  return request.get(getNotificationListUrl, data)
}
// Notification details
export const getNotificationDetail = (notification_id: string) => {
  return request.get(`/notifications/${notification_id}`)
}
// Mark a single notification as read
export const notificationRead = (notification_id: string) => {
  return request.post(`/notifications/${notification_id}/read`)
}
// Mark all notifications as read
export const notificationReadAll = () => {
  return request.post(`/notifications/read-all`)
}
// Close banner
export const notificationBannerClose = (banner_id: string) => {
  return request.post(`/notifications/banners/${banner_id}/close`)
}
// Confirm modal notification
export const notificationModalConfirm = (modal_id: string) => {
  return request.post(`/notifications/${modal_id}/confirm`)
}
// Synchronize notification state /sync
export const notificationSync = () => {
  return request.get(`/notifications/sync`)
}
// SSE real-time events
export const notificationsEvents = (
  onMessage?: (data: SSEMessage[]) => void,
  onAbort?: (abort: () => void) => void,
  onOpen?: () => void,
) => {
  return handleSSE(`/notifications/events`, undefined, onMessage, { method: 'GET', onOpen }, onAbort)
}
