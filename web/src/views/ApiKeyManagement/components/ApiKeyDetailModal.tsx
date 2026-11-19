import { forwardRef, useImperativeHandle, useState } from 'react';
import { Switch, Button, Tooltip, Flex, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ApiKey, ApiKeyModalRef } from '../types';
import RbModal from '@/components/RbModal'
import { getApiKey  } from '@/api/apiKey';
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'
import { maskApiKeys } from '@/utils/apiKeyReplacer';

/**
 * Modal component for viewing API key details
 * Displays read-only information about an API key
 */
const ApiKeyDetailModal = forwardRef<ApiKeyModalRef, { handleCopy: (content: string) => void }>(({ handleCopy }, ref) => {
  // Hooks
  const { t } = useTranslation();
  
  // State
  const [visible, setVisible] = useState(false);
  const [data, setData] = useState<ApiKey>({} as ApiKey)

  /**
   * Close the modal
   */
  const handleClose = () => {
    setVisible(false);
  };

  /**
   * Open modal and fetch API key details
   * @param apiKey - API key item to view
   */
  const handleOpen = (apiKey?: ApiKey) => {
    if (apiKey?.id) {
      getApiKey(apiKey.id)
        .then((res) => {
          setVisible(true);
          setData(res as ApiKey)
        })
    }
  };

  /**
   * Expose methods to parent component via ref
   */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('apiKey.viewDetail')}
      open={visible}
      onCancel={handleClose}
    >
      <Flex vertical gap={12}>
        <div className="rb:text-gray-600 rb:font-medium rb:leading-5">{t('apiKey.baseInfo')}</div>
        {['id', 'name', 'is_expired', 'created_at'].map((key) => (
          <Flex justify="space-between" gap={20} key={key} className="rb:font-regular rb:text-[14px]">
            <span className="rb:text-gray-600">{t(`apiKey.${key}`)}</span>
            <span className="rb:text-right rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">
              { key === 'created_at'
                ? formatDateTime(data[key], 'YYYY-MM-DD HH:mm:ss')
                : key === 'is_expired'
                  ? <Tag color={data[key] ? 'error' : 'processing'}>{data[key] ? t('apiKey.inactive') : t('apiKey.active')}</Tag>
                  : <Tooltip title={String(data[key as keyof ApiKey])}>{String(data[key as keyof ApiKey])}</Tooltip>
              }
            </span>
          </Flex>
        ))}
      </Flex>

      <Flex align="center" justify="space-between" className="rb:text-gray-600 rb:mt-5! rb:py-2! rb:px-4! rb:bg-[#FFFFFF] rb:border rb:border-gray-400 rb:rounded-lg rb:leading-5">
        {maskApiKeys(data.api_key)}

        <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(data.api_key)}>
          <div
            className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
          ></div>
          {t('common.copy')}
        </Button>
      </Flex>

      <Flex vertical gap={12} className="rb:mt-4!">
        <div className="rb:text-gray-600 rb:font-medium rb:leading-5">{t('apiKey.permissionInfo')}</div>

        <Row gutter={[12, 12]}>
          {([
            ['memory', t('apiKey.memoryEngine')],
            ['rag', t('apiKey.knowledgeBase')],
            ['app_mgmt', t('apiKey.app_mgmt')],
            ['asset', t('apiKey.asset')],
          ] as const).map(([scope, label]) => (
            <Col span={12} key={scope}>
              <Flex align="center" justify="space-between" gap={8} className="rb:h-full rb:bg-gray-100 rb:rounded-xl rb:p-3!">
                <span className="rb:text-[14px]">{label}</span>
                <Switch checked={data.scopes?.includes(scope)} disabled />
              </Flex>
            </Col>
          ))}
        </Row>
      </Flex>

      <Flex vertical gap={12} className="rb:mt-4!">
        <div className="rb:text-gray-600 rb:font-medium rb:leading-5 rb:mb-1">{t('apiKey.advancedSettings')}</div>

        {data.expires_at &&
          <Flex justify="space-between" gap={20} className="rb:font-regular rb:text-[14px]">
            <span className="rb:text-gray-600">{t(`apiKey.expires_at`)}</span>
            <span>
              {data.expires_at ? formatDateTime(data.expires_at as number, 'YYYY-MM-DD HH:mm:ss') : '-'}
            </span>
          </Flex>
        }
        <Flex justify="space-between" gap={20} className="rb:font-regular rb:text-[14px]">
          <span className="rb:text-gray-600">{t(`application.qpsLimit`)}</span>
          <span>
            {data.rate_limit} {t('application.qpsLimitUnit')}
          </span>
        </Flex>
        <Flex justify="space-between" gap={20} className="rb:font-regular rb:text-[14px]">
          <span className="rb:text-gray-600">{t(`application.dailyUsageLimit`)}</span>
          <span>
            {data.daily_request_limit} {t('application.dailyUsageLimitUnit')}
          </span>
        </Flex>
      </Flex>
    </RbModal>
  );
});

export default ApiKeyDetailModal;