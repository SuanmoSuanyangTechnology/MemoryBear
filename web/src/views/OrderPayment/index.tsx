/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:37:31 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-08 17:08:55
 */
/**
 * Order Payment Page
 * Displays order details and payment voucher submission form
 * Supports corporate transfer payment method
 */

import React, { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Button, Input, InputNumber, App, Form, DatePicker, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'
import dayjs from 'dayjs';

import corporateImg from '@/assets/images/order/corporate.svg'
import type { OrderForm, UpgradePreview } from './types'
import { useI18n } from '@/store/locale'
import type { Package } from '@/views/Package/types'
import { billingUnits } from '@/views/Package/constant'
import { UnitWrapper } from '@/views/Package'
import { submitOrder, getPackageList, upgradePackagePreview } from '@/api/package'
import Tag from '@/components/Tag'

const { TextArea } = Input;

/** Payment information */
const paymentInfo = {
  payee: '上海算模算样科技有限公司',
  bankName: '交通银行上海同济支行',
  bankAccount: '3100 6634 4013 0082 44111'
};
const OrderPayment: React.FC = () => {
  const location = useLocation();
  const { message, modal } = App.useApp()
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { language } = useI18n()
  const [form] = Form.useForm<OrderForm>()
  
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [pkg, setPkg] = useState<Package | null>(null)
  const [jumpFrom, setJumpFrom] = useState<string | null>(null)
  const multiplierValue = Form.useWatch('multiplier', form)

  /** Copy text to clipboard */
  const copyText = (text: string) => {
    copy(text)
    message.success(t('common.copySuccess'))
  };

  /** Submit payment voucher */
  const submitPayment = (values: OrderForm) => {
    if (isSubmitting || !pkg?.id) return;
    
    setIsSubmitting(true);
    
    const { pay_time, ...rest } = values
    const submitData: OrderForm = {
      ...rest,
      business_type: jumpFrom === 'renewal' ? 'renewal' : jumpFrom === '/upgrade' ? 'upgrade' : 'purchase',
      pay_time: pay_time?.valueOf(),
      package_plan_id: pkg?.id,
    };
    submitOrder(submitData)
      .then(() => {
        form.resetFields()

        modal.confirm({
          title: t('pricing.confirmRedirect'),
          content: t('pricing.confirmRedirectContent'),
          okText: t('pricing.goBack'),
          cancelText: t('pricing.stayCurrentPage'),
          onOk() {
            navigate('/pricing')
          },
        });
      })
      .finally(() => {
        setIsSubmitting(false);
      })
  };

  useEffect(() => {
    setUpgradePreview(null)
    if (location.state?.jumpFrom) {
      setJumpFrom(location.state?.jumpFrom)

      if (location.state?.jumpFrom === 'renewal') {
        getPackageList({ search: location.state?.id }).then(res => {
          setPkg((res as Package[])[0] ? { ...(res as Package[])[0], created_at: dayjs().valueOf() } : null)
        })
      }
    }
    if (!location.state?.id) return
    setPkg({
      ...location.state,
      created_at: dayjs().valueOf(),
    })
    form.setFieldsValue({
      package_plan_id: location.state?.id || location.state?.package_plan_id,
    })
  }, [location, form]);

  const getKeyWithLanguage = (key: string) => {
    return (language === 'en' ? `${key}_en` : key) as keyof Package
  }
  const [upgradePreview, setUpgradePreview] = useState<UpgradePreview | null>(null)
  const getUpgradePreview = () => {
    if (!pkg?.id) return
    upgradePackagePreview({
      target_plan_id: pkg?.id,
      multiplier: multiplierValue,
    })
      .then(res => {
        setUpgradePreview(res as UpgradePreview)
      })
  }

  useEffect(() => {
    if (!multiplierValue || jumpFrom !== '/upgrade') return
    getUpgradePreview()
  }, [jumpFrom, multiplierValue])

  return (
    <Form 
      form={form} 
      layout="vertical"
      onFinish={submitPayment}
      className="rb:space-y-4"
    >
      {(upgradePreview) &&
        <Flex gap={24} align="center" justify="space-between" className="rb:bg-white rb:rounded-2xl rb:px-4! rb:py-2! rb:mb-4!">
          <Flex align="center" gap={12} className="rb:flex-1!">
            <Tag>{t('pricing.upgrade')}</Tag>
            <div className="rb:flex-1">
              <span className="rb:font-medium rb:text-[16px]">{String(pkg?.[getKeyWithLanguage('name')] ?? '')}</span>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:mt-1">
              {t('pricing.upgradeTip', { name: String(pkg?.[getKeyWithLanguage('name')] ?? ''), cycle: `${multiplierValue} ${t(`package.${pkg?.billing_cycle}`)}` })}
              </div>
            </div>
          </Flex>

          <div className="rb:grid rb:grid-cols-2 rb:bg-[#F7F9FC] rb:gap-5 rb:p-3 rb:rounded-lg">
            <div className="rb:text-[12px] rb:text-[#5B6167]">
              {t('pricing.currentExpiredAt')}
              <div className="rb:font-medium rb:text-[16px] rb:text-[#171719]">{dayjs(pkg?.expired_at).format('YYYY-MM-DD')}</div>
            </div>
            <div className="rb:text-[12px] rb:text-[#5B6167]">
              {t('pricing.upgradeExpiredAt')}
              <div className="rb:font-medium rb:text-[16px] rb:text-[#369F21]">{dayjs(upgradePreview?.target_expired_at).format('YYYY-MM-DD')}</div>
            </div>
          </div>
        </Flex>
      }
      {(jumpFrom === 'renewal') &&
        <Flex align="center" justify="space-between" className="rb:bg-white rb:rounded-2xl rb:px-4! rb:py-2! rb:mb-4!">
          <Flex align="center" gap={12}>
            <Tag>{t('pricing.renewal')}</Tag>
            <div>
              <span className="rb:font-medium rb:text-[16px]">{String(pkg?.[getKeyWithLanguage('name')] ?? '')}</span>
              <div className="rb:text-[#5B6167] rb:mt-1">
                {t('pricing.renewalTip', { cycle: `${multiplierValue} ${t(`package.${pkg?.billing_cycle}`)}` })}
              </div>
            </div>
          </Flex>

          <div className="rb:grid rb:grid-cols-2 rb:bg-[#F7F9FC] rb:gap-5 rb:p-3 rb:rounded-lg">
            <div className="rb:text-[12px] rb:text-[#5B6167]">
              {t('pricing.currentExpiredAt')}
              <div className="rb:font-medium rb:text-[16px] rb:text-[#171719]">{dayjs(pkg?.expired_at).format('YYYY-MM-DD')}</div>
            </div>
            <div className="rb:text-[12px] rb:text-[#5B6167]">
              {t('pricing.renewalExpiredAt')}
              <div className="rb:font-medium rb:text-[16px] rb:text-[#369F21]">{dayjs(pkg?.expired_at).add(multiplierValue, pkg?.billing_cycle === 'monthly' ? 'month' : 'year').format('YYYY-MM-DD')}</div>
            </div>
          </div>
        </Flex>
      }
      <div className="rb:h-full rb:overflow-y-auto rb:bg-white rb:rounded-lg rb:py-3 rb:px-3">
        {/* Order Information */}
        <div className="rb:mb-6">
          <h2 className="rb:text-[16px] rb:text-lg rb:font-semibold rb:mb-4">{t('pricing.orderInformation')}</h2>
          
          <div className="rb:flex rb:flex-col rb:items-start rb:gap-8 rb:mb-6 rb:text-[12px] ">
            <div className="rb:flex rb:items-center rb:gap-2">
              <span className="rb:text-[#5B6167]">{t('pricing.creationTime')}:</span>
              <span className="">{dayjs(pkg?.created_at).format('YYYY-MM-DD HH:mm:ss')}</span>
            </div>
          </div>

          {/* Order Details Table */}
          <div className="rb:border rb:border-[#DFE4ED] rb:rounded-2xl">
            <table className="rb:w-full">
              <thead>
                <tr>
                  <th className="rb:px-4 rb:py-2 rb:w-50 rb:font-normal!">{t('pricing.comboName')}</th>
                  <th className="rb:px-4 rb:py-2 rb:font-normal!">{t('pricing.versionInformation')}</th>
                  <th className="rb:px-4 rb:py-2 rb:w-32 rb:text-left rb:font-normal!">{t('pricing.orderCycle')}</th>
                  <th className="rb:px-4 rb:py-2 rb:w-32 rb:text-right rb:font-normal!">{t('pricing.orderAmount')}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="rb:px-4 rb:py-2 rb:w-50">
                    <div className="rb:text-[18px] rb:text-xl rb:font-bold rb:mb-1">{String(pkg?.[getKeyWithLanguage('name')] ?? '')}</div>
                    <div className="rb:text-[12px]  rb:text-[#5B6167]">{String(pkg?.[getKeyWithLanguage('core_value')] ?? '')}</div>
                  </td>
                  <td className="rb:px-4 rb:py-2">
                    <div className="b:flex-1! rb:grid rb:md:grid-cols-2 rb:gap-3 rb:text-[12px] rb:text-[#5B6167]">
                      {billingUnits.map(({ key, unit, icon }) => {
                        const value = pkg?.quotas?.[key as keyof Package['quotas']];
                        return (
                          <UnitWrapper
                            key={key}
                            titleKey={key}
                            value={value}
                            unit={unit}
                            icon={icon}
                            theme_color={pkg?.theme_color}
                          />
                        )
                      })}
                      {pkg?.tech_support && pkg[getKeyWithLanguage('tech_support')] && (
                        <UnitWrapper
                            titleKey="tech_support"
                            value={String(pkg[getKeyWithLanguage('tech_support')] ?? '')}
                            icon="technical_support"
                            theme_color={pkg.theme_color}
                          />
                      )}
                      {pkg?.sla_compliance && pkg[getKeyWithLanguage('sla_compliance')] && (
                        <UnitWrapper
                          titleKey="sla"
                          value={String(pkg[getKeyWithLanguage('sla_compliance')] ?? '')}
                          icon="sla"
                          theme_color={pkg.theme_color}
                        />
                      )}
                    </div>
                  </td>
                  <td className="rb:px-4 rb:py-2 rb:w-32 rb:text-[#5B6167]">
                    <Form.Item name="multiplier" initialValue={1}>
                      <InputNumber min={1} max={100} precision={0} suffix={t(`package.${pkg?.billing_cycle}`)} />
                    </Form.Item>
                  </td>
                  <td className="rb:px-4 rb:py-2">
                    <div className="rb:w-32 rb:text-right rb:font-bold  rb:text-[20px] rb:text-2xl">
                      <span className="rb:text-[#5B6167] rb:font-normal rb:text-[12px] rb:hidden">{t('pricing.orderAmount')}: </span>
                      ¥ {typeof upgradePreview?.amount_due === 'number' ? upgradePreview?.amount_due : Number(pkg?.price || 0) * Number(multiplierValue)}
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* Payment Method and Payment Voucher */}
        <div className="rb:grid rb:grid-cols-2 rb:gap-6">
          {/* Payment Method */}
          <div className="rb:border rb:border-[#DFE4ED] rb:rounded-2xl rb:p-4">
            <h2 className="rb:text-[16px] rb:text-lg rb:font-semibold rb:mb-4">{t('pricing.paymentMethod')}</h2>
            
            <div className="rb:bg-[rgba(255,255,255,0.12)] rb:rounded-2xl rb:p-3 rb:mb-6">
              <div className="rb:flex rb:items-center rb:gap-3">
                <img src={corporateImg} className="rb:size-8" />
                <div>
                  <div className="rb:text-[14px] rb:text-base  rb:font-medium">{t('pricing.corporateTransfer')}</div>
                  <div className="rb:text-[12px] rb:text-[#5B6167]">{t('pricing.corporateTransferDesc')}</div>
                </div>
              </div>
            </div>

            <div>
              <h3 className=" rb:font-medium rb:mb-4">{t('pricing.payeeInformation')}</h3>
              
              <div className="rb:space-y-4 ">
                <div>
                  <div className="rb:text-[#5B6167] rb:mb-1">{t('pricing.receivingEntity')}:</div>
                  <div className="">{paymentInfo.payee}</div>
                </div>
                
                <div>
                  <div className="rb:text-[#5B6167] rb:mb-1">{t('pricing.bankName')}:</div>
                  <div className="">{paymentInfo.bankName}</div>
                </div>
                
                <div>
                  <div className="rb:text-[#5B6167] rb:mb-1">{t('pricing.bankAccountNumber')}:</div>
                  <div className="rb:flex rb:items-center rb:gap-2">
                    <span className=" rb:break-all">{paymentInfo.bankAccount}</span>
                    <div
                      className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:hover:bg-[url('@/assets/images/copy_hover.svg')]"
                      onClick={() => copyText(paymentInfo.bankAccount)}
                    ></div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Payment Voucher */}
          <div className="rb:border rb:border-[#DFE4ED] rb:rounded-2xl rb:p-4">
            <h2 className="rb:text-[16px] rb:text-lg rb:font-semibold rb:mb-4">{t('pricing.paymentVoucher')}</h2>

            <Form.Item 
              name="pay_txn_id" 
              label={t('pricing.pay_txn_id')}
              extra={t('pricing.pay_txn_idDesc')}
              rules={
                [{ required: !(jumpFrom === '/upgrade' && !upgradePreview?.amount_due), message: t('common.pleaseEnter') }]
              }
            >
              <Input placeholder={t('pricing.pay_txn_idPlaceholder')} />
            </Form.Item>
            <Form.Item
              name="payer"
              label={t('pricing.payer')}
              rules={
                [{ required: !(jumpFrom === '/upgrade' && !upgradePreview?.amount_due), message: t('common.pleaseEnter') }]
              }
            >
              <Input placeholder={t('pricing.payerPlaceholder')} />
            </Form.Item>
            <Form.Item
              name="pay_time"
              label={t('pricing.transferDate')}
              rules={
                [{ required: !(jumpFrom === '/upgrade' && !upgradePreview?.amount_due), message: t('common.pleaseSelect') }]
              }
            >
              <DatePicker className="rb:w-full" placeholder={t('common.pleaseSelect')} />
            </Form.Item>
            <Form.Item
              name="remarks"
              label={t('pricing.remark')}
            >
              <TextArea placeholder={t('pricing.remarkPlaceholder')} />
            </Form.Item>

            <Button type="primary" htmlType="submit" loading={isSubmitting} block>
              {t('pricing.confirm')}
            </Button>

            <p className="rb:text-[12px] rb:text-[#5B6167] rb:text-left rb:mt-2">
              {t('pricing.payInfo')}<br />
              {t('pricing.paySuccess')}
            </p>
          </div>
        </div>
      </div>
    </Form>
  );
};

export default OrderPayment;