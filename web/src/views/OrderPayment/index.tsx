import React, { useState, useEffect, useMemo } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { Button, Input, App, Form, DatePicker } from 'antd';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'
import dayjs from 'dayjs';

import { submitPaymentVoucherAPI } from '@/api/order';
import corporateImg from '@/assets/images/order/corporate.svg'
import checkImg from '@/assets/images/order/check.svg'
import type { PriceItem, VoucherForm } from './types'

const { TextArea } = Input;

const paymentInfo = {
  payee: '上海算模算样科技有限公司',
  bankName: '交通银行上海同济支行',
  bankAccount: '3100 6634 4013 0082 44111'
};
const OrderPayment: React.FC = () => {
  const [searchParams] = useSearchParams();
  const { message, modal } = App.useApp()
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [form] = Form.useForm<VoucherForm>()
  
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectedType, setSelectedType] = useState('biz');

  const PRICE_LIST: PriceItem[] = [
    {
      type: 'personal',
      label: 'pricing.personal.label',
      typeDesc: 'pricing.personal.typeDesc',
      priceDescObj: {
        solution: 'pricing.personal.solution',
        targetAudience: 'pricing.personal.targetAudience',
      },
      priceObj: {
        type: 'default',
        price: 0,
        time: 'pricing.personal.priceDesc',
      },
      btnType: 'started',
      memoryCapacity: '2000',
      intelligentSearchFrequency: '100',
    },
    {
      type: 'team',
      label: 'pricing.team.label',
      typeDesc: 'pricing.team.typeDesc',
      priceDescObj: {
        solution: 'pricing.team.solution',
        targetAudience: 'pricing.team.targetAudience',
      },
      priceObj: {
        type: 'default',
        price: 19,
        time: 'pricing.team.priceDesc'
      },
      btnType: 'choosePlan',
      memoryCapacity: '20,000',
      intelligentSearchFrequency: '10,000',
    },
    {
      type: 'biz',
      label: 'pricing.biz.label',
      typeDesc: 'pricing.biz.typeDesc',
      priceDescObj: {
        solution: 'pricing.biz.solution',
        targetAudience: 'pricing.biz.targetAudience',
      },
      mostPopular: true,
      priceObj: {
        type: 'default',
        price: 299,
        time: 'pricing.biz.priceDesc'
      },
      btnType: 'choosePlan',
      memoryCapacity: '100,000',
      intelligentSearchFrequency: '50,000',
    },
    {
      type: 'commerce',
      label: 'pricing.commerce.label',
      typeDesc: 'pricing.commerce.typeDesc',
      priceDescObj: {
        solution: 'pricing.commerce.solution',
        targetAudience: 'pricing.commerce.targetAudience',
      },
      priceObj: {
        type: 'commerce',
        time: 'pricing.commerce.priceDesc'
      },
      btnType: 'contact',
      memoryCapacity: '20,000',
      intelligentSearchFrequency: '10,000',
      flexibleDeployment: true,
      reliableGuarantee: true
    },
  ];

  const selectedPlan = useMemo(() => {
    return PRICE_LIST.find(item => item.type === selectedType) || PRICE_LIST[2];
  }, [selectedType]);

  const translations = useMemo(() => ({
    title: t('pricing.title'),
    desc: t('pricing.desc'),
    mostPopular: t('pricing.mostPopular'),
    startedBtn: t('pricing.startedBtn'),
    choosePlanBtn: t('pricing.choosePlanBtn'),
    contactBtn: t('pricing.contactBtn'),
    memoryCapacity: t('pricing.memoryCapacity'),
    entries: t('pricing.entries'),
    intelligentSearchFrequency: t('pricing.intelligentSearchFrequency'),
    timesMonth: t('pricing.timesMonth'),
    supportServices: t('pricing.supportServices'),
    flexibleDeployment: t('pricing.flexibleDeployment'),
    reliableGuarantee: t('pricing.reliableGuarantee'),
    getItemType: (type: string) => t(`pricing.${type}.type`),
    getItemLabel: (labelKey: string) => t(labelKey),
    getItemDesc: (descKey: string) => t(descKey),
    getPriceKey: (key: string) => t(`pricing.${key}`),
    getItemPriceDesc: (descKey: string) => t(descKey),
    getPriceTime: (timeKey: string) => t(timeKey),
    getTypeSupportService: (type: string, key: string) => t(`pricing.${type}.${key}`),
  }), [t]);

  const getProductType = (type: string) => {
    const typeMap: Record<string, string> = {
      'personal': 'FREE',
      'team': 'TEAM',
      'biz': 'ENTERPRISE',
      'commerce': 'OEM'
    };
    return typeMap[type] || 'ENTERPRISE';
  };

  const generateOrderNumber = () => {
    const date = new Date();
    const dateStr = date.getFullYear().toString() + 
                    (date.getMonth() + 1).toString().padStart(2, '0') + 
                    date.getDate().toString().padStart(2, '0');
    const random = Math.floor(Math.random() * 1000000).toString().padStart(6, '0');
    return `ORD-${dateStr}${random}`;
  };

  const orderInfo = useMemo(() => {
    const plan = selectedPlan;
    return {
      orderNumber: generateOrderNumber(),
      creationTime: dayjs().format('YYYY-MM-DD HH:mm:ss'),
      comboName: t(`pricing.${plan.type}.type`),
      comboEdition: t(plan.typeDesc),
      solutionPositioning: t(plan.priceDescObj.solution),
      targetAudience: t(plan.priceDescObj.targetAudience),
      memoryCapacity: `${plan.memoryCapacity} ${t('pricing.entries')}`,
      searchFrequency: `${plan.intelligentSearchFrequency} ${t('pricing.timesMonth')}`,
      supportServices: t(`pricing.${plan.type}.supportServices`),
      flexibleDeployment: t(`pricing.${plan.type}.flexibleDeployment`),
      reliableGuarantee: t(`pricing.${plan.type}.reliableGuarantee`),
      orderingCycle: '1 month',
      orderAmount: plan.priceObj.price || 'Contact Us'
    };
  }, [selectedPlan, t]);

  const copyText = (text: string) => {
    copy(text)
    message.success(t('common.copySuccess'))
  };

  const submitPayment = (values: VoucherForm) => {
    if (isSubmitting) return;
    
    setIsSubmitting(true);
    
      const submitData = {
        product_type: getProductType(selectedType),
        ...values,
        payable_amount: orderInfo.orderAmount,
        pay_time: values.transferDate.valueOf(),
      };
      submitPaymentVoucherAPI(submitData)
        .then(res => {
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
    const type = searchParams.get('type');
    if (type && PRICE_LIST.find(item => item.type === type)) {
      setSelectedType(type);
    }
  }, [searchParams]);

  return (
    <div className="rb:w-full rb:pb-20">
      {/* 订单信息 */}
      <div className="rb:mb-6">
        <h2 className="rb:text-[16px] rb:text-lg rb:font-semibold rb:mb-4">{t('pricing.orderInformation')}</h2>
        
        <div className="rb:flex rb:flex-col rb:items-start rb:gap-8 rb:mb-6 rb:text-[12px] ">
          <div className="rb:flex rb:items-center rb:gap-2">
            <span className="rb:text-[#5B6167]">{t('pricing.creationTime')}:</span>
            <span className="">{orderInfo.creationTime}</span>
          </div>
        </div>

        {/* 订单详情表格 */}
        <div className="rb:border rb:border-[#DFE4ED] rb:rounded-2xl rb:overflow-hidden">
          {/* 桌面端表头 */}
          <div className="rb:flex rb:gap-4 rb:p-4 rb:bg-[rgba(255,255,255,0.03)] rb:border-b rb:border-b-[rgba(255,255,255,0.1)]">
            <div className="rb:flex-1">{t('pricing.comboName')}</div>
            <div className="rb:flex-2">{t('pricing.spAndTa')}</div>
            <div className="rb:flex-2">{t('pricing.versionInformation')}</div>
            <div className="rb:w-32">{t('pricing.orderCycle')}</div>
            <div className="rb:w-32 rb:text-right">{t('pricing.orderAmount')}</div>
          </div>
          {/* 表格内容 */}
          <div className="rb:flex rb:p-4 rb:flex-row rb:gap-4">
            {/* 套餐名称 */}
            <div className="rb:flex-1">
              <div className="rb:hidden rb:text-[12px] rb:text-[#5B6167] rb:mb-1">{t('pricing.comboName')}</div>
              <div className="rb:text-[18px] rb:text-xl rb:font-bold  rb:mb-1">{orderInfo.comboName}</div>
              <div className="rb:text-[12px]  rb:text-[#5B6167]">{orderInfo.comboEdition}</div>
            </div>
            {/* 解决方案和目标受众 */}
            <div className="rb:flex-2 rb:text-[12px] ">
              <div className="rb:hidden rb:text-[12px] rb:text-[#5B6167] rb:mb-2">{t('pricing.spAndTa')}</div>
              <div className="rb:mb-4">
                <div className=" rb:font-medium rb:mb-1">{translations.getPriceKey('solution')}</div>
                <div className="rb:text-[#5B6167]">{orderInfo.solutionPositioning}</div>
              </div>
              <div>
                <div className=" rb:font-medium rb:mb-1">{translations.getPriceKey('targetAudience')}</div>
                <div className="rb:text-[#5B6167]">{orderInfo.targetAudience}</div>
              </div>
            </div>
            {/* 版本信息 */}
            <div className="rb:flex-2 rb:text-[12px]  rb:space-y-2">
              <div className="rb:hidden rb:text-[12px] rb:text-[#5B6167] rb:mb-2">{t('pricing.versionInformation')}</div>
              <div className="rb:flex rb:items-center rb:gap-2">
                <img src={checkImg} className="rb:w-3 rb:h-3 rb:size-3" />
                <span className="rb:text-[#5B6167]">{translations.memoryCapacity} <span className="">{orderInfo.memoryCapacity}</span></span>
              </div>
              <div className="rb:flex rb:items-center rb:gap-2">
                <img src={checkImg} className="rb:w-3 rb:h-3 rb:size-3" />
                <span className="rb:text-[#5B6167]">{translations.intelligentSearchFrequency} <span className="">{orderInfo.searchFrequency}</span></span>
              </div>
              <div className="rb:flex rb:items-center rb:gap-2">
                <img src={checkImg} className="rb:w-3 rb:h-3 rb:size-3" />
                <span className="rb:text-[#5B6167]">{translations.supportServices} <span className="">{orderInfo.supportServices}</span></span>
              </div>
              {selectedType === 'commerce' && (
                <>
                  <div className="rb:flex rb:items-center rb:gap-2">
                    <img src={checkImg} className="rb:w-3 rb:h-3 rb:size-3" />
                    <span className="rb:text-[#5B6167]">{translations.flexibleDeployment} <span className="">{translations.getTypeSupportService('commerce', 'flexibleDeployment')}</span></span>
                  </div>
                  <div className="rb:flex rb:items-center rb:gap-2">
                    <img src={checkImg} className="rb:w-3 rb:h-3 rb:size-3" />
                    <span className="rb:text-[#5B6167]">{translations.reliableGuarantee} <span className="">{translations.getTypeSupportService('commerce', 'reliableGuarantee')}</span></span>
                  </div>
                </>
              )}
            </div>
            {/* 订购周期和金额 */}
            <div className="rb:w-32 rb:text-[12px]  rb:text-[#5B6167]">
              {orderInfo.orderingCycle}
            </div>
            <div className={`rb:w-32 rb:text-right rb:font-bold  rb:text-[20px] rb:text-xl ${selectedType === 'commerce' ? '' : 'rb:text-2xl'}`}>
              <span className="rb:text-[#5B6167] rb:font-normal rb:text-[12px] rb:hidden">{t('pricing.orderAmount')}: </span>
              {selectedType === 'commerce' ? '' : '$'}{orderInfo.orderAmount}
            </div>
          </div>
        </div>
      </div>

      {/* 支付方式和支付凭证 */}
      <div className="rb:grid rb:grid-cols-2 rb:gap-6">
        {/* 支付方式 */}
        <div className="rb:border rb:border-[#DFE4ED] rb:rounded-2xl rb:p-4">
          <h2 className="rb:text-[16px] rb:text-lg rb:font-semibold rb:mb-4">{t('pricing.paymentMethod')}</h2>
          
          <div className="rb:bg-[rgba(255,255,255,0.12)] rb:rounded-2xl rb:p-3 rb:mb-6">
            <div className="rb:flex rb:items-center rb:gap-3">
              <img src={corporateImg} className="rb:size-8" />
              <div>
                <div className="rb:text-[14px] rb:text-base  rb:font-medium">{t('pricing.corporateTransfer')}</div>
                <div className="rb:text-[12px] rb:text-xs rb:text-[#5B6167]">{t('pricing.corporateTransferDesc')}</div>
              </div>
            </div>
          </div>

          <div>
            <h3 className="rb:text-[12px]  rb:font-medium rb:mb-4">{t('pricing.payeeInformation')}</h3>
            
            <div className="rb:space-y-4 rb:text-[12px] ">
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

        {/* 支付凭证 */}
        <div className="rb:border rb:border-[#DFE4ED] rb:rounded-2xl rb:p-4">
          <h2 className="rb:text-[16px] rb:text-lg rb:font-semibold rb:mb-4">{t('pricing.paymentVoucher')}</h2>
          
          <Form 
            form={form} 
            layout="vertical"
            onFinish={submitPayment}
            className="rb:space-y-4"
          >
            <Form.Item 
              name="pay_txn_id" 
              label={t('pricing.pay_txn_id')} 
              required
              extra={t('pricing.pay_txn_idDesc')}
            >
              <Input placeholder={t('pricing.pay_txn_idPlaceholder')} />
            </Form.Item>
            <Form.Item
              name="payer"
              label={t('pricing.payer')}
              required
            >
              <Input placeholder={t('pricing.payerPlaceholder')} />
            </Form.Item>
            <Form.Item
              name="transferDate"
              label={t('pricing.transferDate')}
              required
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

            <p className="rb:text-[12px] rb:text-[#5B6167] rb:text-left">
              {t('pricing.payInfo')}<br />
              {t('pricing.paySuccess')}
            </p>
          </Form>
        </div>
      </div>
    </div>
  );
};

export default OrderPayment;