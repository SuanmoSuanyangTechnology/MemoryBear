/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:37:23 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-08 15:56:55
 */
/**
 * Price item configuration
 */
export interface PriceItem {
  type: string;
  label: string;
  typeDesc: string;
  priceDescObj: {
    solution: string;
    targetAudience: string;
  };
  priceObj: {
    type: string;
    price?: number;
    time: string;
  };
  btnType: string;
  memoryCapacity: string;
  intelligentSearchFrequency: string;
  mostPopular?: boolean;
  flexibleDeployment?: boolean;
  reliableGuarantee?: boolean;
}

/**
 * Payment voucher form data
 */
export interface VoucherForm {
  pay_txn_id: string;
  payer: string;
  transferDate: string;
  remarks: string;
}

export interface OrderForm {
  package_plan_id: string;
  multiplier: number;
  business_type: string; // 业务类型筛选（可选）：购买purchase/续费renewal/升级upgrade/降级downgrade/recharge/free
  pay_txn_id: string;
  payer: string;
  pay_time: number;
  remarks: string;                                               // 备注说明
}

interface QueuedSub {
  package_name: string;
  package_version: string;
  price: number;
  billing_cycle: string;
  remaining_days: number;
  credit_amount: number;
}
export interface UpgradePreview {
  current_plan_name: string;
  current_plan_price: number;
  current_expired_at: number;
  remaining_days: number;
  total_days: number;
  active_credit_amount: number;
  credit_amount: number;
  queued_subs: QueuedSub[];
  target_plan_name: string;
  target_plan_price: number;
  target_plan_total: number;
  amount_due: number;
  extra_days: number;
  target_expired_at: number;
}