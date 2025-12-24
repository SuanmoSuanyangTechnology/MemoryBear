export interface Query {
  product_type?: string | null;
  status?: string | null;
  search?: string;
  page_index?: number;
  page_size?: number;
  start_time?: number | null;
  end_time?: number | null;
  [key: string]: string | number | null | undefined;
}
export interface Order {
  order_no: string;
  user_id: string;
  tenant_id: string;
  uname: string;
  status: number;
  product_type: string;
  valid: number;
  payable_amount: string;
  pay_status: number;
  pay_txn_id: string;
  pay_time: number;
  payer: string;
  servicer_id: number;
  valid_time: number;
  remarks: string;
  closed: number;
  service_status: number;
  ship_status: number;
  invite_code: string;
  from_view: string;
  tags: string;
  app_id: number;
  id: number;
  optimistic: number;
  create_time: number;
  update_time: number;
  deleted: number;
}

export interface OrderDetailRef {
  handleOpen: (order: Order) => void;
}
