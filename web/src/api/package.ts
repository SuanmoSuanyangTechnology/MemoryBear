import { request } from '@/utils/request'

import type { Package } from '@/views/Package/types'
import type { OrderForm } from '@/views/OrderPayment/types'
// Package list
export const getPackageListUrl = `/package-plans`
export const getPackageList = (query?: { category?: Package['category']; status?: boolean; search?: string; }) => {
  return request.get(getPackageListUrl, query)
}

// Order list
export const orderListUrl = '/tenant/orders';
// Submit order
export const submitOrder = (data: OrderForm) => {
  return request.post('/tenant/orders', data)
}
// Order details
export const getOrderDetail = (order_id: string) => {
  return request.get(`/tenant/orders/${order_id}`)
}
// Upgrade package discount preview
export const upgradePackagePreview = (data: { target_plan_id: string; multiplier: number }) => {
  return request.get('/tenant/orders/upgrade-preview', data)
}
