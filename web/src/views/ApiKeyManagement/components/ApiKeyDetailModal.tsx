import { forwardRef, useImperativeHandle, useState } from 'react';
import { Switch, Button, Tooltip } from 'antd';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import type { ApiKey, ApiKeyModalRef } from '../types';
import RbModal from '@/components/RbModal'
import { getApiKey  } from '@/api/apiKey';
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'
import { maskApiKeys } from '@/utils/apiKeyReplacer';

const ApiKeyDetailModal = forwardRef<ApiKeyModalRef, { handleCopy: (content: string) => void }>(({ handleCopy }, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [data, setData] = useState<ApiKey>({} as ApiKey)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
  };

  const handleOpen = (apiKey?: ApiKey) => {
    if (apiKey?.id) {
      getApiKey(apiKey.id)
        .then((res) => {
          setVisible(true);
          setData(res as ApiKey)
        })
    }
  };

  // 暴露给父组件的方法
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
      <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:mb-4">{t('apiKey.baseInfo')}</div>
      {['id', 'name', 'is_expired', 'created_at'].map((key, index) => (
        <div key={key} className={clsx("rb:flex rb:justify-between rb:gap-5 rb:font-regular rb:text-[14px]", {
          'rb:mt-3': index !== 0
        })}>
          <span className="rb:text-[#5B6167]">{t(`apiKey.${key}`)}</span>
          <span className="rb:text-right rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">
            { key === 'created_at'
              ? formatDateTime(data[key], 'YYYY-MM-DD HH:mm:ss')
              : key === 'is_expired'
                ? <Tag color={data[key] ? 'error' : 'processing'}>{data[key] ? t('apiKey.inactive') : t('apiKey.active')}</Tag>
                : <Tooltip title={String(data[key as keyof ApiKey])}>{String(data[key as keyof ApiKey])}</Tooltip>
            }
          </span>
        </div>
      ))}

      <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:mt-5 rb:p-[8px_16px] rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:leading-5">
        {maskApiKeys(data.api_key)}

        <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(data.api_key)}>
          <div
            className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
          ></div>
          {t('common.copy')}
        </Button>
      </div>

      <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:my-4">{t('apiKey.permissionInfo')}</div>

      <div className="rb:flex rb:justify-between rb:gap-5 rb:font-regular rb:text-[14px] rb:mt-3">
        <span className="rb:text-[#5B6167]">{t(`apiKey.memoryEngine`)}</span>
        <span>
          <Switch checked={data.scopes?.includes('memory')} disabled />
        </span>
      </div>
      <div className="rb:flex rb:justify-between rb:gap-5 rb:font-regular rb:text-[14px] rb:mt-3">
        <span className="rb:text-[#5B6167]">{t(`apiKey.knowledgeBase`)}</span>
        <span>
          <Switch checked={data.scopes?.includes('rag')} disabled />
        </span>
      </div>

      {/* 高级设置 */}
      {data.expires_at && <>
        <div className="rb:text-[#5B6167] rb:font-medium rb:leading-5 rb:my-4">{t('apiKey.advancedSettings')}</div>

        <div className="rb:flex rb:justify-between rb:gap-5 rb:font-regular rb:text-[14px] rb:mt-3">
          <span className="rb:text-[#5B6167]">{t(`apiKey.expires_at`)}</span>
          <span>
            {data.expires_at ? formatDateTime(data.expires_at as number, 'YYYY-MM-DD HH:mm:ss') : '-'}
          </span>
          </div>
      </>}
    </RbModal>
  );
});

export default ApiKeyDetailModal;