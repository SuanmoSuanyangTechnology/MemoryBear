import React, { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, App, Space } from 'antd';
import clsx from 'clsx';
import { DeleteOutlined, EditOutlined, EyeOutlined } from '@ant-design/icons';
import type { ApiKey, ApiKeyModalRef } from './types';
import ApiKeyModal from './components/ApiKeyModal';
import ApiKeyDetailModal from './components/ApiKeyDetailModal';
import RbCard from '@/components/RbCard/Card'
import { getApiKeyListUrl, deleteApiKey } from '@/api/apiKey';
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format';
import Tag from '@/components/Tag'
import copy from 'copy-to-clipboard'
import { maskApiKeys } from '@/utils/apiKeyReplacer';

const ApiKeyManagement: React.FC = () => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const apiKeyModalRef = useRef<ApiKeyModalRef>(null);
  const apiKeyDetailModalRef = useRef<ApiKeyModalRef>(null)
  const scrollListRef = useRef<PageScrollListRef>(null)

  const refresh = () => {
    scrollListRef.current?.refresh();
  }
  
  const handleEdit = (item?: ApiKey) => {
    apiKeyModalRef.current?.handleOpen(item);
  }
  const handleView = (item: ApiKey) => {
    apiKeyDetailModalRef.current?.handleOpen(item);
  }
  const handleDelete = (item: ApiKey) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      okType: 'danger',
      onOk: () => {
      deleteApiKey(item.id)
        .then(() => {
          refresh();
          message.success(t('common.deleteSuccess'))
        })
      }
    })
  }
  const handleCopy = (content: string) => {
    copy(content)
    message.success(t('common.copySuccess'))
  }
  return (
    <>
      <div className="rb:flex rb:justify-end rb:mb-3 rb:p-4">
        <Button type="primary" onClick={() => handleEdit()}>
          {t('apiKey.createApiKey')}
        </Button>
      </div>

      <PageScrollList
        ref={scrollListRef}
        url={getApiKeyListUrl}
        query={{ is_active: true, type: 'service' }}
        column={2}
        renderItem={(item: Record<string, unknown>) => {
          let apiKeyItem = item as unknown as ApiKey
          return (
            <RbCard 
              title={apiKeyItem.name}
          >
            {['id', 'is_expired', 'created_at'].map((key, index) => (
              <div key={key} className={clsx("rb:flex rb:justify-between rb:gap-5 rb:font-regular rb:text-[14px]", {
                'rb:mt-3': index !== 0
              })}>
                <span className="rb:text-[#5B6167] rb:w-20">{t(`apiKey.${key}`)}</span>
                <span className="rb:flex-1 rb:text-left rb:py-px rb:rounded rb:font-medium">
                  { key === 'created_at'
                    ? formatDateTime(apiKeyItem[key], 'YYYY-MM-DD HH:mm:ss')
                    : key === 'is_expired'
                      ? <Tag color={apiKeyItem[key] ? 'error' : 'processing'}>{apiKeyItem[key] ? t('apiKey.inactive') : t('apiKey.active')}</Tag>
                    : String(apiKeyItem[key as keyof ApiKey])
                  }
                </span>
              </div>
            ))}

            <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:mt-5 rb:p-[8px_16px] rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:leading-5">
              {maskApiKeys(apiKeyItem.api_key)}
              
              <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(apiKeyItem.api_key)}>
                <div 
                  className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]" 
                ></div>
                {t('common.copy')}
              </Button>
            </div>

            <Space className="rb:pt-2 rb:min-h-6.25">
              {apiKeyItem.scopes?.includes('memory') && <Tag>{t('apiKey.memoryEngine')}</Tag>}
              {apiKeyItem.scopes?.includes('rag') && <Tag color="success">{t('apiKey.knowledgeBase')}</Tag>}
            </Space>

            <div className="rb:mt-5 rb:flex rb:justify-end rb:gap-2.5">
              <Button icon={<EyeOutlined />} onClick={() => handleView(apiKeyItem)}></Button>
              <Button icon={<EditOutlined />} onClick={() => handleEdit(apiKeyItem)}></Button>
              <Button icon={<DeleteOutlined />} onClick={() => handleDelete(apiKeyItem)}></Button>
            </div>
            </RbCard>
          );
        }}
      />

      <ApiKeyModal
        ref={apiKeyModalRef}
        refresh={refresh}
      />
      <ApiKeyDetailModal
        ref={apiKeyDetailModalRef}
        handleCopy={handleCopy}
      />
    </>
  );
};

export default ApiKeyManagement;