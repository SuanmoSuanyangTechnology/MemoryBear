import type { FaqStatus, Source } from './types';
import type { TagProps } from '@/components/Tag';
import { type StatusTagProps } from '@/components/StatusTag';

export const sourceStatusColors: Record<Source['source_status'], TagProps['color']> = {
  none: 'default',
  available: 'success',
  unavailable: 'error',
};

export const statusColors: Record<FaqStatus, StatusTagProps['status']> = {
  pending: 'warning',
  approved: 'success',
  change_pending: 'warning',
};