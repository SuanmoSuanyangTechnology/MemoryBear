/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:35:49 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-20 10:35:25
 */
/**
 * Order | GroupOrder Detail Component
 * Modal displaying detailed order information including payment details
 */

import { forwardRef, useImperativeHandle, useState, useMemo } from 'react';
import { Descriptions, type DescriptionsProps } from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';

import type { Order, GroupOrder, OrderDetailRef } from '../types'
import RbModal from '@/components/RbModal'
import { STATUS } from '../constant';
import { getOrderDetail } from '@/api/package'
import { formatDateTime } from '@/utils/format';

interface OrderDetailProps {
  getProductName: (order: Order | GroupOrder) => string;
}

const OrderDetail = forwardRef<OrderDetailRef, OrderDetailProps>(({ getProductName }, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [data, setData] = useState<Order | GroupOrder | null>(null)

  /** Close modal */
  const handleClose = () => {
    setVisible(false);
  };

  /** Open modal and fetch order details */
  const handleOpen = (order: Order | GroupOrder) => {
    setVisible(true);
    const order_no = (order as GroupOrder).orders?.length ? (order as GroupOrder).orders[0].id : (order as Order).id
    getOrderDetail(order_no)
      .then(res => {
        setData(res as Order | GroupOrder)
      })
  };

  /** Format order information items */
  const formatItems = useMemo(() => {
    if (!data) return []
    const items: DescriptionsProps['items'] = [
      {
        key: 'order_no',
        label: t('pricing.order_no'),
        children: (data as GroupOrder).order_group_id || (data as Order).order_no || '-'
      },
      {
        key: 'business_type',
        label: t('pricing.business_type'),
        children: (data as GroupOrder).orders?.length
          ? t(`pricing.${(data as GroupOrder).orders[0].business_type}`)
          : t(`pricing.${(data as Order).business_type}`)
},
      {
        key: 'status',
        label: t('pricing.status'),
        children: <span className={data.status === 'rejected' ? 'rb:text-[#FF5D34]' : ''}>{data.status ? t(`pricing.${STATUS[data.status].key}`) : '-'}</span>
      },
      {
        key: 'package_snapshot',
        label: t('pricing.package_snapshot'),
        children: getProductName(data)
      },
      {
        key: 'payable_amount',
        label: t('pricing.payable_amount'),
        children: data.payable_amount != null
          ? `￥${Number(data.payable_amount).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          : '-'
      },
      {
        key: 'created_at',
        label: t('pricing.created_at'),
        children: (data as GroupOrder).orders?.length
          ? formatDateTime((data as GroupOrder).orders[0].created_at)
          : formatDateTime((data as Order).created_at)
      }
    ]
    return items
  }, [data, t, getProductName])

  /** Format payment voucher items */
  const formatPayItems = useMemo(() => {
    if (!data) return []
    const items: DescriptionsProps['items'] = [
      {
        key: 'payer',
        label: t('pricing.payer'),
        children: data.payer || '-'
      },
      {
        key: 'pay_txn_id',
        label: t('pricing.pay_txn_id'),
        children: data.pay_txn_id || '-'
      },
      {
        key: 'pay_time',
        label: t('pricing.transferDate'),
        children: data.pay_time ? dayjs(data.pay_time).format('YYYY-MM-DD HH:mm:ss') : '—'
      },
    ]
    return items
  }, [data, t])

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('pricing.orderDetail')}
      open={visible}
      footer={null}
      onCancel={handleClose}
      width={600}
    >
      <Descriptions
        title={t('pricing.orderInfo')}
        column={1}
        items={formatItems as DescriptionsProps['items']}
        classNames={{ label: 'rb:w-50 rb:text-[#5B6167]!', title: 'rb:font-medium! rb:text-[#5B6167]!' }}
      />
      <Descriptions
        title={t('pricing.paymentVoucher')}
        column={1}
        items={formatPayItems as DescriptionsProps['items']}
        classNames={{ label: 'rb:w-50 rb:text-[#5B6167]!', title: 'rb:font-medium! rb:text-[#5B6167]!' }}
        className="rb:mt-6!"
      />
    </RbModal>
  );
});

export default OrderDetail;