import { request } from '@/utils/request'

import type { Package } from '@/views/Package/types'
// 套餐列表
export const getPackageListUrl = `/package-plans`
export const getPackageList = (query?: { category?: Package['category']; status?: boolean; }) => {
  return request.get(getPackageListUrl, query)
}