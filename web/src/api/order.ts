import { request } from '@/utils/request'
import type { VoucherForm } from '@/views/OrderPayment/types'

export const getOrderListUrl = '/v1/orders/customer'

// 提交支付凭证API
export const submitPaymentVoucherAPI = (voucherData: VoucherForm) => {
  return request.post('/v1/orders/', voucherData)
}
// 订单详情
export const getOrderDetail = (order_no: string) => {
  return request.get(`/v1/orders/customer/${order_no}`)
}
export const orderStatusUrl = '/v1/order-status/'
export const getOrderStatus = () => {
  return request.get(orderStatusUrl)
}