/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:52:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-04 10:00:01
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Switch, App, DatePicker, Button, Flex, Result } from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs'

import type { ApiKey, ApiKeyModalRef } from '../types';
import RbModal from '@/components/RbModal'
import { createApiKey, updateApiKey  } from '@/api/apiKey';
import { stringRegExp } from '@/utils/validator';
import RbSlider from '@/components/RbSlider'
import DebounceSelect from '@/components/DebounceSelect'
import { userMemoryListUrl } from '@/api/memory'
import type { Data } from '@/views/UserMemory/types'

const FormItem = Form.Item;

/**
 * Props for ApiKeyModal component
 */
interface CreateModalProps {
  /** Callback to refresh parent list after save */
  refresh: () => void;
}

interface CreatedApiKey {
  id: string;
  name: string;
  description: string;
  api_key: string;
  type: string;
  scopes: string[];
  resource_id: string;
  rate_limit: number;
  daily_request_limit: number;
  is_active: boolean;
  expires_at: number;
  created_at: number;
  end_user_id?: string;
  other_id: string;
  is_expired: boolean;
}
/**
 * Modal component for creating or editing API keys
 * Handles API key configuration including permissions and expiration
 */
const ApiKeyModal = forwardRef<ApiKeyModalRef, CreateModalProps>(({
  refresh,
}, ref) => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<ApiKey>();
  
  // State
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [editVo, setEditVo] = useState<ApiKey | null>(null);
  const [showSuccessModal, setShowSuccessModal] = useState(false);
  const [createdApiKey, setCreatedApiKey] = useState<CreatedApiKey | null>(null);

  const memory = Form.useWatch('memory', form);

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    message.success(t('common.copySuccess'));
  };

  const generateDefaultUserId = () => {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
    let random = '';
    for (let i = 0; i < 12; i++) {
      random += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    form.setFieldsValue({
      user_id: random,
      end_user: [{ value: random }]
    })
  };

  /**
   * Close modal and reset form state
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditVo(null);
  };

  /**
   * Open modal for creating or editing
   * @param apiKey - Optional API key data for edit mode
   */
  const handleOpen = (apiKey?: ApiKey) => {
    if (apiKey?.id) {
      const { scopes = [], expires_at, ...rest } = apiKey
      // Edit mode - populate form with existing data
      form.setFieldsValue({
        ...rest,
        memory: scopes.includes('memory'),
        rag: scopes.includes('rag'),
        expires_at: expires_at ? dayjs(expires_at) : undefined,
      });
      setEditVo(apiKey);
    } else {
      generateDefaultUserId()
    }
    setVisible(true);
  };

  /**
   * Validate and submit form data
   * Creates new API key or updates existing one
   */
  const handleSave = async () => {
    form.validateFields()
      .then((values) => {
        const { memory, rag, expires_at, user_id, ...rest } = values
        const scopes = []

        if (memory) {
          scopes.push('memory')
        }
        if (rag) {
          scopes.push('rag')
        }
        // Prepare new/updated API key data
        const apiKeyData = {
          ...rest,
          scopes,
          expires_at: expires_at ? dayjs(expires_at.valueOf()).endOf('day').valueOf() : null,
          type: 'service',
          user_id: memory ? user_id : undefined
        };
        setLoading(true)
        const req = editVo?.id ? updateApiKey(editVo.id, apiKeyData as ApiKey) : createApiKey(apiKeyData as ApiKey)
        
        req.then((result) => {
            refresh();
            handleClose();
            if (!editVo?.id && result) {
              setCreatedApiKey(result as CreatedApiKey);
              setShowSuccessModal(true);
            } else {
              message.success(t(editVo ? 'common.updateSuccess' : 'common.createSuccess'));
            }
          })
          .finally(() => setLoading(false))
      })
  }
  const handleChangeEndUser = (value: any[]) => {
    const endUser = value[value.length - 1] || undefined
    form.setFieldsValue({
      end_user: endUser ? [endUser] : undefined,
      user_id: endUser?.value || undefined
    })
  }

  /**
   * Expose methods to parent component via ref
   */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={showSuccessModal ? t('common.createSuccess') : (editVo ? t('apiKey.updateApiKey') : t('apiKey.createApiKey'))}
      open={visible || showSuccessModal}
      onCancel={showSuccessModal ? () => setShowSuccessModal(false) : handleClose}
      okText={showSuccessModal ? t('common.close') : t('common.save')}
      onOk={showSuccessModal ? () => setShowSuccessModal(false) : handleSave}
      confirmLoading={loading}
      footer={showSuccessModal ? null : undefined}
    >
      {showSuccessModal ? (
        <>
          <Result
            status="success"
            title={t('apiKey.apiKeyCreated')}
            subTitle={t('apiKey.apiKeyShowOnce')}
            className="rb:pt-0! rb:pb-4!"
          />

          <div className="rb:mb-4">
            <div className="rb:text-sm rb:text-gray-500 rb:mb-2">{t('apiKey.id')}</div>
            <Flex align="center" justify="space-between" className="rb:bg-[#F6F6F6] rb:text-[#5B6167] rb:rounded-lg rb:px-3! rb:py-2!">
              <span className="rb:text-sm">{createdApiKey?.other_id}</span>
 
              <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(createdApiKey?.other_id || '')}>
                <div
                  className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
                ></div>
                {t('common.copy')}
              </Button>
            </Flex>
          </div>

          <div className="rb:mb-4">
            <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
              <span className="rb:text-sm rb:text-gray-500">{t('apiKey.apiKey')}</span>
              <span className="rb:text-xs rb:text-orange-500">{t('apiKey.showOnce')}</span>
            </div>
            <Flex align="center" justify="space-between" className="rb:bg-[#F6F6F6] rb:text-[#5B6167] rb:rounded-lg rb:px-3! rb:py-2!">
              <span className="rb:text-sm">{createdApiKey?.api_key}</span>

              <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(createdApiKey?.api_key || '')}>
                <div
                  className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
                ></div>
                {t('common.copy')}
              </Button>
            </Flex>
          </div>

          {createdApiKey?.end_user_id && (
            <div className="rb:mb-6">
              <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
                <span className="rb:text-sm rb:text-gray-500">{t('apiKey.endUserId')}</span>
                <span className="rb:text-xs rb:text-orange-500">{t('apiKey.pleaseSave')}</span>
              </div>
              <Flex align="center" justify="space-between" className="rb:bg-[#F6F6F6] rb:text-[#5B6167] rb:rounded-lg rb:px-3! rb:py-2!">
                <span className="rb:text-sm">{createdApiKey?.end_user_id}</span>

                <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(createdApiKey?.end_user_id || '')}>
                  <div
                    className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
                  ></div>
                  {t('common.copy')}
                </Button>
              </Flex>
            </div>
          )}

          <div className="rb-border-t rb:pt-4">
            <div className="rb:text-sm rb:text-gray-500 rb:font-medium rb:mb-2">{t('apiKey.basicInfo')}</div>
            <div className="rb:flex rb:justify-between rb:items-center rb:py-2">
              <span className="rb:text-gray-600">{t('apiKey.name')}</span>
              <span>{createdApiKey?.name}</span>
            </div>
            <div className="rb:flex rb:justify-between rb:items-center rb:py-2">
              <span className="rb:text-gray-600">{t('apiKey.createdAt')}</span>
              <span>{createdApiKey?.created_at ? dayjs(createdApiKey.created_at).format('YYYY-MM-DD HH:mm:ss') : ''}</span>
            </div>
          </div>
        </>
      ) : (
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            rate_limit: 50,
            daily_request_limit: 100000
          }}
        >
          <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.baseInfo')}</div>
          <FormItem
            name="name"
            label={t('apiKey.name')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
              { max: 50 },
              { pattern: stringRegExp, message: t('common.nameInvalid') },
            ]}
          >
            <Input placeholder={t('common.enter')} />
          </FormItem>
          
          <FormItem
            name="description"
            label={t('apiKey.description')}
            rules={[{ max: 500 }]}
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

          {!editVo && <>
            <FormItem name="user_id" hidden />

            
            {memory &&
              <Flex align="center" justify="space-between" className="rb:mb-1!">
                <div>
                  <span className="rb:text-[#ff5d34]">*</span>
                  {t('apiKey.memoryDefaultId')}
                </div>
                <Button
                  type="link"
                  onClick={generateDefaultUserId}
                >
                  {t('apiKey.regenerate')}
                </Button>
              </Flex>
            }
            <FormItem
              name="end_user"
              dependencies={['memory']}
              rules={[
                { required: memory, message: t('common.pleaseEnter') },
              ]}
              hidden={!memory}
            >
              <DebounceSelect
                url={userMemoryListUrl}
                mode="tags"
                searchKey="keyword"
                format={(items) => (items as Data[]).map(item => ({
                  ...item,
                  'end_user.id': item.end_user?.id,
                  label: <Flex align="center" gap={12}>{item.end_user?.id} <span className="rb:text-[#5B6167] rb:text-[12px]">{t('apiKey.existingUser')}</span></Flex>,
                  value: item.end_user?.id,
                }))}
                placeholder={t('memoryConversation.searchPlaceholder')}
                showSearch
                allowClear
                onChange={handleChangeEndUser}
              />
            </FormItem>
          </>}

          <FormItem
            name="rag"
            label={t('apiKey.knowledgeBase')}
            layout="horizontal"
            valuePropName="checked"
          >
            <Switch />
          </FormItem>

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
          <FormItem
            name="rate_limit"
            label={<>{t(`application.qpsLimit`)}({t('application.qpsLimitTip')}, {t('application.qpsLimitUnit')})</>}
            extra={t('application.qpsLimitDesc')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
            ]}
          >
            <RbSlider
              min={1}
              max={100}
              step={1}
              isInput={true}
            />
          </FormItem>
          <FormItem
            name="daily_request_limit"
            label={<>{t(`application.dailyUsageLimit`)} ({t('application.dailyUsageLimitUnit')})</>}
            extra={t('application.dailyUsageLimitDesc')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
            ]}
          >
            <RbSlider
              min={100}
              max={100000}
              step={100}
              isInput={true}
            />
          </FormItem>
        </Form>
      )}
    </RbModal>
  );
});

export default ApiKeyModal;