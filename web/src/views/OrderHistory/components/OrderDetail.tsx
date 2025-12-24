import { forwardRef, useImperativeHandle, useState, useCallback } from 'react';
import { Descriptions } from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';

import type { Order, OrderDetailRef } from '../types'
import RbModal from '@/components/RbModal'
import { getOrderDetail } from '@/api/order'
import { STATUS } from '../index';


const OrderDetail = forwardRef<OrderDetailRef>((_props, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [data, setData] = useState({})

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
  };

  const handleOpen = (order: Order) => {
    setVisible(true);
    getOrderDetail(order.order_no)
      .then(res => {
        setData(res as Order)
      })
  };
  const formatItems = useCallback(() => {
    if (!data) return []
    return ['order_no', 'product_type', 'payable_amount', 'status', 'pay_time', 'create_time'].map(key => {
      const value = (data as any)[key]
      return {
        key,
        label: t(`pricing.${key}`),
        children: ['pay_time', 'create_time'].includes(key) && value
          ? dayjs(value).format('YYYY-MM-DD HH:mm:ss')
          : key === 'status' && value
            ? t(`pricing.${STATUS[value as keyof typeof STATUS].key}`)
            : key === 'product_type' && value
              ? t(`pricing.${value.toLowerCase()}.type`)
              : value
      }
    })
  }, [data])
  const formatPayItems = useCallback(() => {
    if (!data) return []
    return ['pay_txn_id', 'payer'].map(key => ({
      key,
      label: t(`pricing.${key}`),
      children: (data as any)[key]
    }))
  }, [data])

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  // ['pay_txn_id', 'payer']
  // ['pay_txn_id', 'payer']
  return (
    <RbModal
      title={t('pricing.orderDetail')}
      open={visible}
      footer={null}
      onCancel={handleClose}
      width={1000}
    >
      <Descriptions title={t('pricing.orderInfo')} column={2} items={formatItems()} classNames={{ label: 'rb:w-40' }} />
      <Descriptions title={t('pricing.orderPayInfo')} column={2} items={formatPayItems()} classNames={{ label: 'rb:w-40' }} className="rb:mt-6!" />
    </RbModal>
  );
});

export default OrderDetail;