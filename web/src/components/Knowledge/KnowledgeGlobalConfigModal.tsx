/**
 * Knowledge Global Configuration Modal
 * Configures global reranker settings for all knowledge bases
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, InputNumber } from 'antd';
import { useTranslation } from 'react-i18next';

import type { RerankerConfig, KnowledgeGlobalConfigModalRef } from './types'
import RbModal from '@/components/RbModal'
import ModelSelect from '@/components/ModelSelect'
import RadioGroupCard from '@/components/RadioGroupCard'
import WeightBalanceSlider from './WeightBalanceSlider'

const FormItem = Form.Item;

interface KnowledgeGlobalConfigModalProps {
  data: RerankerConfig;
  refresh: (values: RerankerConfig, type: 'rerankerConfig') => void;
}

const KnowledgeGlobalConfigModal = forwardRef<KnowledgeGlobalConfigModalRef, KnowledgeGlobalConfigModalProps>(({
  refresh,
  data,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<RerankerConfig>();
  const values = Form.useWatch<RerankerConfig>([], form);

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = () => {
    form.setFieldsValue({ ...data, rerank_mode: data.rerank_mode || 'reranking_model' })
    setVisible(true);
  };

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

  const handleChangeMode = (value?: string | null) => {
    if (value === 'reranking_model') {
      form.setFieldValue('rerank_weights', null)
    } else if (value === 'weighted_score') {
      form.setFieldsValue({
        reranker_id: null,
        rerank_weights: {
          semantic_weight: 1,
          participle_weight: 0,
        }
      })
    }
  }

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
        size="middle"
        initialValues={{
          rerank_mode: 'reranking_model',
        }}
      >
        <div className="rb:text-[#5B6167] rb:mb-6">{t('application.globalConfigDesc')}</div>

        <Form.Item name="rerank_mode">
          <RadioGroupCard
            options={[
              { label: t('application.reranking_model'), value: 'reranking_model' },
              { label: t('application.weighted_score'), value: 'weighted_score' },
            ]}
            onChange={value => handleChangeMode(value)}
          />
        </Form.Item>

        <FormItem
          name="reranker_id"
          label={t('application.rearrangementModel')}
          rules={[{ required: values?.rerank_mode === 'reranking_model', message: t('common.pleaseSelect') }]}
          extra={t('application.rearrangementModelDesc')}
          hidden={values?.rerank_mode !== 'reranking_model'}
        >
          <ModelSelect
            params={{ type: 'rerank' }}
            className="rb:w-full!"
          />
        </FormItem>

        {values?.rerank_mode === 'weighted_score' && <>
          <Form.Item
            required
            label={t('application.weight_balance')}
          >
            <WeightBalanceSlider
              semanticWeight={values?.rerank_weights?.semantic_weight}
              participleWeight={values?.rerank_weights?.participle_weight}
              onChange={(semanticWeight, participleWeight) => {
                form.setFieldsValue({
                  rerank_weights: {
                    semantic_weight: semanticWeight,
                    participle_weight: participleWeight,
                  },
                });
              }}
            />
          </Form.Item>
        </>}
        <Form.Item name="rerank_weights" hidden />
        <FormItem
          name="reranker_top_k"
          label={t('application.reranker_top_k')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
          extra={t('application.reranker_top_k_desc')}
        >
          <InputNumber
            className="rb:w-full!"
            placeholder={t('common.pleaseEnter')}
            min={1}
            max={20}
            onChange={(value) => form.setFieldValue('reranker_top_k', value)}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default KnowledgeGlobalConfigModal