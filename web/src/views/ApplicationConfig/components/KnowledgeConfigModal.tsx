import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Form, Select, InputNumber } from 'antd';
import { useTranslation } from 'react-i18next';

import type { KnowledgeConfigModalRef, KnowledgeBase, KnowledgeConfigForm } from '../types'
import RbModal from '@/components/RbModal'
import RbSlider from '@/components/RbSlider'
import { formatDateTime } from '@/utils/format';

const FormItem = Form.Item;

interface KnowledgeConfigModalProps {
  refresh: (values: KnowledgeConfigForm, type: 'knowledgeConfig') => void;
}
const retrieveTypes = ['participle', 'semantic', 'hybrid']

const KnowledgeConfigModal = forwardRef<KnowledgeConfigModalRef, KnowledgeConfigModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<KnowledgeConfigForm>();
  const [data, setData] = useState<KnowledgeBase | null>(null);

  const values = Form.useWatch<KnowledgeConfigForm>([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setData(null)
  };

  const handleOpen = (data: KnowledgeBase) => {
    form.setFieldsValue({
      retrieve_type: retrieveTypes[0],
      kb_id: data.id,
      ...(data || {}),
      ...(data?.config || {}),
    })
    setData({...data})
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
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

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  useEffect(() => {
    if (values?.retrieve_type) {
      const initialValues = Object.keys(values).map(key => {
        return {
          [key as keyof KnowledgeConfigForm]: (key === 'kb_id' || key === 'retrieve_type') ? values[key] : undefined
        }
      })
      form.resetFields(initialValues)
    }
  }, [values?.retrieve_type])

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
      >
        {data && (
          <div className="rb:mb-[24px] rb:flex rb:items-center rb:justify-between rb:border rb:rounded-[8px] rb:p-[17px_16px] rb:cursor-pointer rb:bg-[#F0F3F8] rb:border-[#DFE4ED] rb:text-[#212332]">
            <div className="rb:text-[16px] rb:leading-[22px]">
              {data.name}
              <div className="rb:text-[12px] rb:leading-[16px] rb:text-[#5B6167] rb:mt-[8px]">{t('application.contains', {include_count: data.doc_num})}</div>
            </div>
            <div className="rb:text-[12px] rb:leading-[16px] rb:text-[#5B6167]">{formatDateTime(data.updated_at, 'YYYY-MM-DD HH:mm:ss')}</div>
          </div>
        )}
        <FormItem name="kb_id" hidden />
        {/* 检索模式 */}
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
          />
        </FormItem>
        {/* Top K */}
        <FormItem
          name="top_k"
          label={t('application.top_k')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
          extra={t('application.top_k_desc')}
        >
          <InputNumber style={{ width: '100%' }} />
        </FormItem>
        {/* 语义相似度阈值 similarity_threshold */}
        {values?.retrieve_type === 'semantic' && (
          <FormItem
            name="similarity_threshold"
            label={t('application.similarity_threshold')}
            extra={t('application.similarity_threshold_desc')}
          >
            <RbSlider 
              max={1.0}
              step={0.1}
              min={0.0}
            />
          </FormItem>
        )}
        {/* 分词匹配度阈值 vector_similarity_weight */}
        {values?.retrieve_type === 'participle' && (
          <FormItem
            name="vector_similarity_weight"
            label={t('application.vector_similarity_weight')}
            extra={t('application.vector_similarity_weight_desc')}
          >
            <RbSlider 
              max={1.0}
              step={0.1}
              min={0.0}
            />
          </FormItem>
        )}
        {/* 混合检索权重 */}
        {values?.retrieve_type === 'hybrid' && (
          <>
            <FormItem
              name="similarity_threshold"
              label={t('application.similarity_threshold')}
              extra={t('application.similarity_threshold_desc1')}
            >
              <RbSlider 
                max={1.0}
                step={0.1}
                min={0.0}
              />
            </FormItem>
            <FormItem
              name="vector_similarity_weight"
              label={t('application.vector_similarity_weight')}
              extra={t('application.vector_similarity_weight_desc1')}
            >
              <RbSlider 
                max={1.0}
                step={0.1}
                min={0.0}
              />
            </FormItem>
          </>
        )}
      </Form>
    </RbModal>
  );
});

export default KnowledgeConfigModal;