import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ConfigModalData, ConfigModalRef } from '../types'
import { getWorkspaceModels, updateWorkspaceModels } from '@/api/workspaces'
import { getModelListUrl } from '@/api/models'
import CustomSelect from '@/components/CustomSelect'
import RbModal from '@/components/RbModal'

const ConfigModal = forwardRef<ConfigModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ConfigModalData>();
  const [loading, setLoading] = useState(false)

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = () => {
    getWorkspaceModels().then((res) => {
      const { llm, embedding, rerank } = res as ConfigModalData
      form.setFieldsValue({
        llm,
        embedding,
        rerank
      })
    })
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        updateWorkspaceModels(values)
          .then(() => {
            setLoading(false)
            handleClose()
            message.success(t('common.updateSuccess'))
          })
          .catch(() => {
            setLoading(false)
          });

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

  return (
    <RbModal
      title={t(`userMemory.editConfig`)}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item 
          label={t('space.llmModel')} 
          name="llm"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={getModelListUrl}
            params={{ type: 'llm', pagesize: 100 }}
            valueKey="id"
            labelKey="name"
            hasAll={false}
            style={{width: '100%'}}
          />
        </Form.Item>
        <Form.Item 
          label={t('space.embeddingModel')} 
          name="embedding"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={getModelListUrl}
            params={{ type: 'embedding', pagesize: 100 }}
            valueKey="id"
            labelKey="name"
            hasAll={false}
            style={{width: '100%'}}
          />
        </Form.Item>
        <Form.Item 
          label={t('space.rerankModel')} 
          name="rerank"
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={getModelListUrl}
            params={{ type: 'rerank', pagesize: 100 }}
            valueKey="id"
            labelKey="name"
            hasAll={false}
            style={{width: '100%'}}
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default ConfigModal;