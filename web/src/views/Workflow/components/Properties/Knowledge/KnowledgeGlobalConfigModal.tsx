import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, InputNumber, Switch } from 'antd';
import { useTranslation } from 'react-i18next';

import type { RerankerConfig, KnowledgeGlobalConfigModalRef } from './types'
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import { getModelListUrl } from '@/api/models'

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

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = () => {
    form.setFieldsValue({ ...data, rerank_model: !!data?.reranker_id })
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
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

  // 暴露给父组件的方法
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

        {/* 结果重排 */}
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
              params={{ type: 'rerank', pagesize: 100 }}
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
            <InputNumber style={{ width: '100%' }} min={1} max={20} />
          </FormItem>
        </>}
      </Form>
    </RbModal>
  );
});

export default KnowledgeGlobalConfigModal;