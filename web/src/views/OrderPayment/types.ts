/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:37:23 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:37:23 
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