/**
 * Multi-Key Configuration Modal
 * Modal for managing multiple API keys for a single model
 * Allows adding and removing API keys
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ModelListItem, ProviderModelItem, MultiKeyForm, MultiKeyConfigModalRef, MultiKeyConfigModalProps, Provider } from '../types';
import RbModal from '@/components/RbModal'
import {
  getModelApiKeys, addModelApiKey, deleteModelApiKey, getModelProviderList,
  getProviderApiKeys, createProviderApiKeys, deleteProviderApiKeys,
} from '@/api/models'

type Model = ModelListItem | ProviderModelItem

interface ProviderApiKey {
  id: string;
  provider: Provider;
  credential_masked: string;
  is_provider_level: boolean;
  model_names: string[];
  api_base: null | string;
  is_active: false;
  priority: number;
  source: string;
  remark: string;
  created_at_ms: number;
  updated_at_ms: number;
}
/**
 * Multi-key configuration modal component
 */
const MultiKeyConfigModal = forwardRef<MultiKeyConfigModalRef, MultiKeyConfigModalProps>(({
  refresh,
  source
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [model, setModel] = useState<Model>({} as Model);
  const [form] = Form.useForm<MultiKeyForm>();
  const [loading, setLoading] = useState(false)
  const [abortController, setAbortController] = useState<AbortController | null>(null)
  const [currentProvider, setCurrentProvider] = useState<Provider | null>(null)
  const [apiKeys, setApiKeys] = useState<ProviderApiKey[]>([])

  /** Close modal and refresh parent */
  const handleClose = () => {
    abortController?.abort()
    setAbortController(null)
    setModel({} as Model);
    refresh?.()

    form.resetFields();
    setLoading(false)
    setVisible(false);
    setCurrentProvider(null)
    setApiKeys([])
  };

  /** Open modal with model data */
  const handleOpen = (vo: Model, provider?: Provider) => {
    setVisible(true);
    if (!provider) {
      setModel(vo as Model)
      getModelProviderList().then((res) => {
        const providerList = res as Provider[]
        const filter = providerList.find(item => item.provider === vo.provider)
        setCurrentProvider(filter || null)
      })
    } else {
      setCurrentProvider(provider);
    }
    getApiKeys((vo as ModelListItem)?.id, provider)
  };

  const getApiKeys = (model_id?: string, provider?: Provider | null) => {
    if (!provider && !model_id) return;
    const request = source === 'provider' && typeof provider === 'string'
      ? getProviderApiKeys({ provider })
      : typeof model_id === 'string'
      ? getModelApiKeys(model_id)
      : null

    if (request) {
      request
        .then(res => {
          setApiKeys((res as { items: ProviderApiKey[] })?.items || res as ProviderApiKey[])
        })
    }
  }
  /** Add new API key */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        const controller = new AbortController()
        setAbortController(controller)
        const modelInfo = model as ModelListItem

        const request = source === 'provider' && currentProvider
          ? createProviderApiKeys({
            ...values,
            provider: currentProvider
          }, controller.signal)
          : addModelApiKey(modelInfo.id, {
            ...values,
            model_config_id: modelInfo.id,
            model_name: modelInfo.name,
            provider: modelInfo.provider,
          }, controller.signal)

        request
          .then(() => {
            form.resetFields();
            message.success(t('common.saveSuccess'))
            getApiKeys((model as ModelListItem).id, currentProvider)
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
    const request = source === 'provider'
      ? deleteProviderApiKeys(api_key_id)
      : deleteModelApiKey((model as ModelListItem).id, api_key_id);
    request
      .then(() => {
        message.success(t('common.deleteSuccess'))
        getApiKeys((model as ModelListItem).id, currentProvider)
      })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={`${(model as ModelListItem).name || (String(currentProvider).charAt(0).toUpperCase() + String(currentProvider).slice(1))} - ${t('modelNew.keyConfig')}`}
      open={visible}
      onCancel={handleClose}
      footer={null}
    >
      {apiKeys.length > 0 && (
        <div className="rb:mb-4">
          {apiKeys.map((key) => (
            <Flex align="center" justify="space-between" gap={12} key={key.id} className="rb:p-3! rb:bg-gray-100 rb:rounded-lg rb:mb-2!">
              <div className="rb:flex-1">
                <div className="rb:text-[14px] rb:font-medium rb:break-all">{key.credential_masked}</div>
                <div className="rb:text-gray-600 rb:text-[12px] rb:mt-1">{key.api_base}</div>
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

        {source !== 'provider' &&
          <>
            <Form.Item
              name="api_base"
              label={t('modelNew.api_base')}
              rules={[{ required: source !== 'provider' && !currentProvider?.default_api_base, message: t('common.inputPlaceholder', { title: t('modelNew.api_base') }) }]}
            >
              <Input placeholder="https://api.example.com/v1" />
            </Form.Item>
          </>
        }

        {source === 'provider' &&
          <>
            <Form.Item
              name="remark"
              label={t('modelNew.remark')}
            >
              <Input placeholder={t('common.pleaseEnter')} />
            </Form.Item>
          </>
        }

        <Form.Item>
          <Button type="primary" block onClick={handleSave} loading={loading}>+ {t('modelNew.add')}</Button>
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default MultiKeyConfigModal;