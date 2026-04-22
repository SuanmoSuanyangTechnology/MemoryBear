/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:52:50 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-22 11:34:06
 */
import React, { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, App, Flex } from 'antd';
import clsx from 'clsx';
import copy from 'copy-to-clipboard'

import type { ApiKey, ApiKeyModalRef } from './types';
import ApiKeyModal from './components/ApiKeyModal';
import ApiKeyDetailModal from './components/ApiKeyDetailModal';
import RbCard from '@/components/RbCard'
import MoreDropdown from '@/components/MoreDropdown'
import useDeleteConfirm from '@/hooks/useDeleteConfirm'
import { getApiKeyListUrl, deleteApiKey } from '@/api/apiKey';
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format';
import Tag from '@/components/Tag'
import { maskApiKeys } from '@/utils/apiKeyReplacer';
import RbDescriptions from '@/components/RbDescriptions';

/**
 * API Key Management page component
 * Manages service API keys with CRUD operations
 */
const ApiKeyManagement: React.FC = () => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const deleteConfirm = useDeleteConfirm();
  
  // Refs
  const apiKeyModalRef = useRef<ApiKeyModalRef>(null);
  const apiKeyDetailModalRef = useRef<ApiKeyModalRef>(null)
  const scrollListRef = useRef<PageScrollListRef>(null)

  /**
   * Refresh the API key list
   */
  const refresh = () => {
    scrollListRef.current?.refresh();
  }
  
  /**
   * Open modal to create or edit API key
   * @param item - Optional API key item for edit mode
   */
  const handleEdit = (item?: ApiKey) => {
    apiKeyModalRef.current?.handleOpen(item);
  }
  
  /**
   * Open modal to view API key details
   * @param item - API key item to view
   */
  const handleView = (item: ApiKey) => {
    apiKeyDetailModalRef.current?.handleOpen(item);
  }
  
  /**
   * Delete API key with confirmation
   * @param item - API key item to delete
   */
  const handleDelete = (item: ApiKey) => {
    deleteConfirm({
      name: item.name,
      onOk: () => deleteApiKey(item.id).then(refresh),
    })
  }
  /**
   * Copy content to clipboard
   * @param content - Content to copy
   */
  const handleCopy = (content: string) => {
    copy(content)
    message.success(t('common.copySuccess'))
  }
  return (
    <>
      <Flex justify="flex-end" className="rb:mb-3!">
        <Button type="primary" onClick={() => handleEdit()}>
          {t('apiKey.createApiKey')}
        </Button>
      </Flex>

      <PageScrollList<ApiKey, { is_active: boolean; type: string }>
        ref={scrollListRef}
        url={getApiKeyListUrl}
        query={{ is_active: true, type: 'service' }}
        column={3}
        renderItem={(apiKeyItem) => {
          return (
            <RbCard
              title={ 
                <Flex justify="space-between" className="rb:w-full!">
                  <Flex gap={4} vertical className="rb:flex-1!">
                    {apiKeyItem.name}
                    <Flex gap={6}>
                      {apiKeyItem.scopes?.includes('memory') && <Tag>{t('apiKey.memoryEngine')}</Tag>}
                      {apiKeyItem.scopes?.includes('rag') && <Tag color="success">{t('apiKey.knowledgeBase')}</Tag>}
                      {!apiKeyItem.scopes?.includes('memory') && !apiKeyItem.scopes?.includes('rag') && <div className="rb:font-regular!">{t('apiKey.noScopes')}</div>}
                    </Flex>
                  </Flex>
                  <MoreDropdown
                    items={[
                      {
                        key: 'edit',
                        icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]" />,
                        label: t('common.edit'),
                        onClick: () => handleEdit(apiKeyItem),
                      },
                      {
                        key: 'view',
                        icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/eye.svg')]" />,
                        label: t('common.view'),
                        onClick: () => handleView(apiKeyItem),
                      },
                      {
                        key: 'delete',
                        danger: true,
                        icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                        label: t('common.delete'),
                        onClick: () => handleDelete(apiKeyItem),
                      },
                    ]}
                  />
                </Flex>
              }
              isNeedTooltip={false}
              headerClassName="rb:min-h-[78px]!"
            >
              <RbDescriptions
                items={['id', 'is_expired', 'created_at'].map(key => ({
                  key,
                  label: t(`apiKey.${key}`),
                  children: <span className={clsx({
                    'rb:font-medium': key === 'id',
                  })}>
                    {key === 'created_at'
                      ? formatDateTime(apiKeyItem[key], 'YYYY-MM-DD HH:mm:ss')
                      : key === 'is_expired'
                        ? <Tag color={apiKeyItem[key] ? 'error' : 'processing'}>{apiKeyItem[key] ? t('apiKey.inactive') : t('apiKey.active')}</Tag>
                        : String(apiKeyItem[key as keyof ApiKey])
                    }
                  </span>
                }))}
              />

              <Flex align="center" justify="space-between" className="rb:h-8! rb:mt-4! rb:py-1! rb:pl-2.5! rb:pr-1! rb:bg-[#F6F6F6] rb:rounded-md rb:leading-5">
                {maskApiKeys(apiKeyItem.api_key)}
                
                <div onClick={() => handleCopy(apiKeyItem.api_key)} className="rb:cursor-pointer rb:rounded-md rb:size-6 rb:bg-[url('@/assets/images/common/copy_dark.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat rb:hover:bg-[rgba(0,0,0,0.08)]"></div>
              </Flex>
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