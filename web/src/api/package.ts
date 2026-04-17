import { request } from '@/utils/request'

import type { Package } from '@/views/Package/types'

export const SYS_API_PREFIX = '/sys';
// 套餐列表
export const getPackageListUrl = `${SYS_API_PREFIX}/package-plans`
export const getPackageList = (query: { category: Package['category']; status: boolean; }) => {
  return request.get(getPackageListUrl, query)
}
// 获取套餐详情
export const getPackageDetail = (package_plan_id: string) => {
  return request.get(`${SYS_API_PREFIX}/package-plans/${package_plan_id}`)
}