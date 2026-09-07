/**
 * Knowledge Configuration Modal
 * Configures retrieval settings for individual knowledge bases
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Select, InputNumber, Flex, Switch } from 'antd';
import { useTranslation } from 'react-i18next';

import type { KnowledgeConfigModalRef, KnowledgeBase, KnowledgeConfigForm, RetrieveType } from './types'
import RbModal from '@/components/RbModal'
import RbSlider from '@/components/RbSlider'
import { formatDateTime } from '@/utils/format';
import ModelSelect from '@/components/ModelSelect'
import RadioGroupButton from '@/components/RadioGroupButton'
import WeightBalanceSlider from './WeightBalanceSlider'

const FormItem = Form.Item;

interface KnowledgeConfigModalProps {
  refresh: (values: KnowledgeConfigForm, type: 'knowledgeConfig') => void;
}

const retrieveTypes: RetrieveType[] = ['participle', 'semantic', 'hybrid', 'graph']

const KnowledgeConfigModal = forwardRef<KnowledgeConfigModalRef, KnowledgeConfigModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<KnowledgeConfigForm>();
  const [data, setData] = useState<KnowledgeBase | null>(null);

  const values = Form.useWatch<KnowledgeConfigForm>([], form);

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setData(null)
  };

  const handleOpen = (data: KnowledgeBase) => {
    form.setFieldsValue({
      retrieve_type: data?.config?.retrieve_type || retrieveTypes[0],
      kb_id: data.id,
      top_k: data?.config?.top_k || 5,
      similarity_threshold: data?.config?.similarity_threshold || 0.5,
      vector_similarity_weight: data?.config?.vector_similarity_weight || 0.5,
      ...(data || {}),
      ...(data?.config || {}),
    })
    setData({...data})
    setVisible(true);
  };

  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        refresh(values, 'knowledgeConfig')
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
    handleClose
  }));

  return (
    <RbModal
      title={t('application.knowledgeConfig')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
        size="middle"
      >
        {data && (
          <Flex align="center" justify="space-between" className="rb:mb-6! rb-border rb:rounded-lg rb:p-[17px_16px]! rb:cursor-pointer rb:bg-[#F0F3F8] rb:text-[#212332]">
            <div className="rb:text-[16px] rb:leading-5.5">
              {data.name}
              <div className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167] rb:mt-2">{t('application.contains', { include_count: data.doc_num })}</div>
            </div>
            <div className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167]">{formatDateTime(data.updated_at, 'YYYY-MM-DD HH:mm:ss')}</div>
          </Flex>
        )}
        <FormItem name="kb_id" hidden />
        <FormItem
          name="retrieve_type"
          label={t('application.retrieve_type')}
          extra={t('application.retrieve_type_desc')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            options={retrieveTypes.map(key => ({
              label: t(`application.${key}`),
              value: key,
            }))}
            placeholder={t('common.pleaseSelect')}
            onChange={(value) => {
              if (value === 'hybrid') {
                form.setFieldValue('rerank_mode', 'reranking_model')
              }
            }}
          />
        </FormItem>
        {(values?.retrieve_type === 'hybrid') && <>
          <Form.Item
            name="enable_graph_retrieval"
            getValueProps={(value: 0 | 1 | undefined) => ({ checked: value === 1 })}
            getValueFromEvent={(checked: boolean) => checked ? 1 : 0}
            initialValue={0}
            label={t('knowledgeBase.hybridIsHasGraph')}
            layout="horizontal"
          >
            <Switch checkedChildren={t('knowledgeBase.yes')} unCheckedChildren={t('knowledgeBase.no')} />
          </Form.Item>

          <Form.Item name="rerank_mode"
            label={t('application.rerank_mode')}
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <RadioGroupButton
              circle={false}
              size="default"
              variant="outlined"
              block={true}
              options={[
                { label: t('application.reranking_model'), value: 'reranking_model' },
                { label: t('application.weighted_score'), value: 'weighted_score' },
              ]}
              onChange={(value) => handleChangeMode(value)}
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
        </>}
        <FormItem
          name="top_k"
          label={t('application.top_k')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
          extra={t('application.top_k_desc')}
        >
          <InputNumber
            className="rb:w-full!"
            placeholder={t('common.pleaseEnter')}
            min={1}
            max={20}
            onChange={(value) => form.setFieldValue('top_k', value)}
          />
        </FormItem>
        {!['participle', 'semantic', 'graph'].includes(values?.retrieve_type || '') &&
          <FormItem
            name="similarity_threshold"
            label={t('application.similarity_threshold')}
            extra={t('application.similarity_threshold_desc')}
            initialValue={0.5}
          >
            <RbSlider
              max={1.0}
              step={0.1}
              min={0.0}
              isInput={true}
            />
          </FormItem>
        }
        {!['participle', 'graph'].includes(values?.retrieve_type || '') &&
          <FormItem
            name="vector_similarity_weight"
            label={t('application.vector_similarity_weight')}
            extra={t('application.vector_similarity_weight_desc')}
            initialValue={0.5}
          >
            <RbSlider
              max={1.0}
              step={0.1}
              min={0.0}
              isInput={true}
            />
          </FormItem>
        }
      </Form>
    </RbModal>
  );
});

export default KnowledgeConfigModal