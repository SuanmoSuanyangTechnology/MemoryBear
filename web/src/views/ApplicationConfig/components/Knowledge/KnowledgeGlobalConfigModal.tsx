/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:42 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:25:42 
 */
/**
 * Knowledge Global Configuration Modal
 * Configures global reranker settings for all knowledge bases
 */

import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, InputNumber, Switch } from 'antd';
import { useTranslation } from 'react-i18next';

import type { RerankerConfig, KnowledgeGlobalConfigModalRef } from './types'
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import { getModelListUrl } from '@/api/models'

const FormItem = Form.Item;

/**
 * Component props
 */
interface KnowledgeGlobalConfigModalProps {
  /** Current reranker configuration */
  data: RerankerConfig;
  /** Callback to update reranker configuration */
  refresh: (values: RerankerConfig, type: 'rerankerConfig') => void;
}

/**
 * Modal for configuring global reranker settings
 */
const KnowledgeGlobalConfigModal = forwardRef<KnowledgeGlobalConfigModalRef, KnowledgeGlobalConfigModalProps>(({
  refresh,
  data,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<RerankerConfig>();
  const values = Form.useWatch<RerankerConfig>([], form);

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  /** Open modal with current configuration */
  const handleOpen = () => {
    form.setFieldsValue({ ...data, rerank_model: !!data?.reranker_id })
    setVisible(true);
  };
  /** Save reranker configuration */
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        refresh(values, 'rerankerConfig')
        handleClose()
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  useEffect(() => {
    if (values?.rerank_model) {
      form.setFieldsValue({ ...data })
    } else {
      form.setFieldsValue({ reranker_id: undefined, reranker_top_k: undefined })
    }
  }, [values?.rerank_model])

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('application.globalConfig')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <div className="rb:text-[#5B6167] rb:mb-6">{t('application.globalConfigDesc')}</div>

        {/* Result reranking */}
        <div className="rb:flex rb:items-center rb:justify-between rb:my-6">
          <div className="rb:text-[14px] rb:font-medium rb:leading-5">
            {t('application.rerankModel')}
            <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t('application.rerankModelDesc')}</div>
          </div>
          <FormItem
            name="rerank_model"
            valuePropName="checked"
            className="rb:mb-0!"
          >
            <Switch />
          </FormItem>
        </div>

        {values?.rerank_model && <>
          <FormItem
            name="reranker_id"
            label={t('application.rearrangementModel')}
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
            extra={t('application.rearrangementModelDesc')}
          >
            <CustomSelect
              url={getModelListUrl}
              params={{ type: 'rerank', pagesize: 100, is_active: true }}
              valueKey="id"
              labelKey="name"
              hasAll={false}
            />
          </FormItem>
          {/* Top K */}
          <FormItem
            name="reranker_top_k"
            label={t('application.reranker_top_k')}
            rules={[{ required: true, message: t('common.pleaseEnter') }]}
            extra={t('application.reranker_top_k_desc')}
          >
            <InputNumber
              style={{ width: '100%' }}
              min={1}
              max={20}
              onChange={(value) => form.setFieldValue('reranker_top_k', value)}
            />
          </FormItem>
        </>}
      </Form>
    </RbModal>
  );
});

export default KnowledgeGlobalConfigModal;