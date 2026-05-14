import type { Order } from './types'
import type { StatusTagProps } from '@/components/StatusTag'
/** Order status mapping */
export const STATUS: Record<Order['status'], { status: StatusTagProps['status']; key: string }> = {
  pending: {
    status: 'warning',
    key: 'PENDING'
  },
  approved: {
    key: 'APPROVED',
    status: 'success'
  },
  rejected: {
    key: 'REJECTED',
    status: 'error'
  },
}

export const typeMap: Record<string, string> = {
  'FREE': 'personal',
  'TEAM': 'team',
  'ENTERPRISE': 'biz',
  'OEM': 'commerce'
};