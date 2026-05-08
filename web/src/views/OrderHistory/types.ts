/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:35:32 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-08 17:35:57
 */

import type { Package } from '@/views/Package/types';

/**
 * Order query parameters
 */
export interface Query {
  product_type?: string | null;
  status?: string | null;
  page_size?: number;
  business_type?: string | null;
}
/**
 * Order data structure
 */
export interface Order {
  id: string;
  order_no: string;
  from_view: 'platform' | 'tenant_self';
  status: 'pending' | 'approved' | 'rejected';
  tenant_id: string;
  user_id: string;
  tenant_name: string;
  user_email: string;
  package_plan_id: string;
  package_version: string;
  product_type: string;
  package_snapshot: Package;
  business_type: 'purchase' | 'renewal' | 'upgrade' | 'recharge' | 'downgrade' | 'free';
  multiplier: number;
  payable_amount: string;
  payment_method: 'bank_transfer' | 'paypal';
  pay_txn_id: string;
  payer: string;
  pay_time: number;
  remarks: string;
  servicer_id: string | null;
  subscription_id: string;
  subscription: null;
  valid_time?: number | null;
  reject_reason: string | null;
  created_by: string | null;
  created_at: number;
  updated_at: number;
}

/**
 * Order detail component ref interface
 */
export interface OrderDetailRef {
  handleOpen: (order: Order) => void;
}
