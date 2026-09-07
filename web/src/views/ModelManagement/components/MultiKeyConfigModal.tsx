/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:49:55 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 12:24:41
 */
/**
 * Multi-Key Configuration Modal
 * Modal for managing multiple API keys for a single model
 * Allows adding and removing API keys
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ModelListItem, MultiKeyForm, MultiKeyConfigModalRef, MultiKeyConfigModalProps, Provider } from '../types';
import RbModal from '@/components/RbModal'
import { addModelApiKey, deleteModelApiKey, getModelInfo, getModelProviderList } from '@/api/models'

/**
 * Multi-key configuration modal component
 */
const MultiKeyConfigModal = forwardRef<MultiKeyConfigModalRef, MultiKeyConfigModalProps>(({ refresh }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<ModelListItem>({} as ModelListItem);
  const [form] = Form.useForm<MultiKeyForm>();
  const [loading, setLoading] = useState(false)
  const [abortController, setAbortController] = useState<AbortController | null>(null)
  const [currentProvider, setCurrentProvider] = useState<Provider | null>(null)

  /** Close modal and refresh parent */
  const handleClose = () => {
    abortController?.abort()
    setAbortController(null)
    setModel({} as ModelListItem);
    refresh?.()

    form.resetFields();
    setLoading(false)
    setVisible(false);
    setCurrentProvider(null)
  };

  /** Open modal with model data */
  const handleOpen = (vo: ModelListItem) => {
    setVisible(true);
    getData(vo)
    getModelProviderList().then((res) => {
      const providerList = res as Provider[]
      const filter = providerList.find(item => item.provider === vo.provider)
      setCurrentProvider(filter || null)
    })
  };

  /** Fetch model information */
  const getData = (vo: ModelListItem) => {
    if (!vo.id) return

    getModelInfo(vo?.id)
      .then(res => {
        setModel(res as ModelListItem)
      })
  }
  /** Add new API key */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        const controller = new AbortController()
        setAbortController(controller)
        addModelApiKey(model.id, {
          ...values,
          model_config_id: model.id,
          model_name: model.name,
          provider: model.provider,
        }, controller.signal).then(() => {
            message.success(t('common.saveSuccess'))
            form.resetFields();
            getData(model)
          })
          .finally(() => {
            setLoading(false)
          });
      })
      .catch((err) => {
        console.log('err', err)
      });
  }
  /** Delete API key */
  const handleDelete = (api_key_id: string) => {
    deleteModelApiKey(api_key_id)
      .then(() => {
        message.success(t('common.deleteSuccess'))
        getData(model)
      })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={`${model.name} - ${t('modelNew.keyConfig')}`}
      open={visible}
      onCancel={handleClose}
      footer={null}
    >
      {model.api_keys && model.api_keys.length > 0 && (
        <div className="rb:mb-4">
          {model.api_keys.map((key) => (
            <Flex align="center" justify="space-between" gap={12} key={key.id} className="rb:p-3! rb:bg-[#F5F6F7] rb:rounded-lg rb:mb-2!">
              <div className="rb:flex-1">
                <div className="rb:text-[#1D2129] rb:text-[14px] rb:font-medium rb:break-all">{key.api_key}</div>
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:mt-1">{key.api_base}</div>
              </div>
              <Button type="primary" danger ghost onClick={() => handleDelete(key.id)}>{t('common.remove')}</Button>
            </Flex>
          ))}
        </div>
      )}
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="api_key"
          label={t('modelNew.api_key')}
          rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('modelNew.api_key') }) }]}
        >
          <Input.Password placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item
          name="api_base"
          label={t('modelNew.api_base')}
          rules={[{ required: !currentProvider?.default_api_base, message: t('common.inputPlaceholder', { title: t('modelNew.api_base') }) }]}
        >
          <Input placeholder="https://api.example.com/v1" />
        </Form.Item>

        <Form.Item>
          <Button type="primary" block onClick={handleSave} loading={loading}>+ {t('modelNew.add')}</Button>
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default MultiKeyConfigModal;