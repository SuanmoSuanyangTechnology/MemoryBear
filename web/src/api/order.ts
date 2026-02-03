/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:00:14 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:00:14 
 */
import { request } from '@/utils/request'
import type { VoucherForm } from '@/views/OrderPayment/types'

export const getOrderListUrl = '/v1/orders/customer'

// Submit payment voucher API
export const submitPaymentVoucherAPI = (voucherData: VoucherForm) => {
  return request.post('/v1/orders/', voucherData)
}
// Order details
export const getOrderDetail = (order_no: string) => {
  return request.get(`/v1/orders/customer/${order_no}`)
}
// Order status enum
export const orderStatusUrl = '/v1/order-status/'
export const getOrderStatus = () => {
  return request.get(orderStatusUrl)
}