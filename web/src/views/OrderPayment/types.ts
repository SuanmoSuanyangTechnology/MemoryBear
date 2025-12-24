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

export interface VoucherForm {
  pay_txn_id: string;
  payer: string;
  transferDate: string;
  remarks: string;
}