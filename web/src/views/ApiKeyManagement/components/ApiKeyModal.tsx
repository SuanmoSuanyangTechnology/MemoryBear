import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Switch, App, DatePicker } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ApiKey, ApiKeyModalRef } from '../types';
import RbModal from '@/components/RbModal'
import dayjs from 'dayjs'
import { createApiKey, updateApiKey  } from '@/api/apiKey';

const FormItem = Form.Item;

interface CreateModalProps {
  refresh: () => void;
}

const ApiKeyModal = forwardRef<ApiKeyModalRef, CreateModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ApiKey>();
  const [loading, setLoading] = useState(false);
  const [editVo, setEditVo] = useState<ApiKey | null>(null);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditVo(null);
  };

  const handleOpen = (apiKey?: ApiKey) => {
    if (apiKey?.id) {
      const { scopes = [], expires_at } = apiKey
      // 编辑模式，填充表单
      form.setFieldsValue({
        name: apiKey.name,
        description: apiKey.description,
        memory: scopes.includes('memory'),
        rag: scopes.includes('rag'),
        expires_at: expires_at ? dayjs(expires_at) : undefined
      });
      setEditVo(apiKey);
    }
    setVisible(true);
  };

  // 封装保存方法，添加提交逻辑
  const handleSave = async () => {
    form.validateFields()
      .then((values) => {
        const { memory, rag, expires_at, ...rest } = values
        let scopes = []

        if (memory) {
          scopes.push('memory')
        }
        if (rag) {
          scopes.push('rag')
        }
        // 准备新的/更新的API Key数据
        const apiKeyData = {
          ...rest,
          scopes,
          expires_at: expires_at ? dayjs(expires_at.valueOf()).endOf('day').valueOf() : null,
          type: 'service'
        };
        setLoading(true)
        const req = editVo?.id ? updateApiKey(editVo.id, apiKeyData as ApiKey) : createApiKey(apiKeyData as ApiKey)
        
        req.then(() => {
            refresh();
            handleClose();
            message.success(t(editVo ? 'common.updateSuccess' : 'common.createSuccess'));
          })
          .finally(() => setLoading(false))
      })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={editVo ? t('apiKey.updateApiKey') : t('apiKey.createApiKey')}
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
        <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.baseInfo')}</div>
        <FormItem
          name="name"
          label={t('apiKey.name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        
        <FormItem
          name="description"
          label={t('apiKey.description')}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} rows={3} />
        </FormItem>

        <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.permissionInfo')}</div>

        <FormItem
          name="memory"
          label={t('apiKey.memoryEngine')}
          layout="horizontal"
          valuePropName="checked"
        >
          <Switch />
        </FormItem>

        <FormItem
          name="rag"
          label={t('apiKey.knowledgeBase')}
          layout="horizontal"
          valuePropName="checked"
        >
          <Switch />
        </FormItem>

        {/* 高级设置 */}
        <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.advancedSettings')}</div>

        <FormItem
          name="expires_at"
          label={t('apiKey.expires_at')}
        >
          <DatePicker
            className="rb:w-full"
            disabledDate={(current) => current && current < dayjs().subtract(1, 'day').endOf('day')}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ApiKeyModal;