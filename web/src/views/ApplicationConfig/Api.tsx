/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:29:29 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 15:31:36
 */
import { type FC, useState, useRef, useEffect } from 'react';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { Button, Space, App, Row, Col, Flex } from 'antd';
import copy from 'copy-to-clipboard'

import type { Application } from '@/views/ApplicationManagement/types'
import type { ApiKeyModalRef, ApiKeyConfigModalRef } from './types'
import type { ApiKey } from '@/views/ApiKeyManagement/types'
import ApiKeyModal from './components/ApiKeyModal';
import ApiKeyConfigModal from './components/ApiKeyConfigModal';
import { getApiKeyList, getApiKeyStats, deleteApiKey } from '@/api/apiKey';
import { maskApiKeys } from '@/utils/apiKeyReplacer'
import RbCard from '@/components/RbCard/Card';

/**
 * API configuration page component
 * Manages API endpoints and API keys for the application
 * @param application - Current application data
 */
const Api: FC<{ application: Application | null }> = ({ application }) => {
  const { t } = useTranslation();
  const activeMethods = ['POST'];
  const { message, modal } = App.useApp()
  const copyContent = window.location.origin + '/v1/app/chat'
  const apiKeyModalRef = useRef<ApiKeyModalRef>(null);
  const apiKeyConfigModalRef = useRef<ApiKeyConfigModalRef>(null);
  const [apiKeyList, setApiKeyList] = useState<ApiKey[]>([])

  /**
   * Copy content to clipboard
   * @param content - Content to copy
   */
  const handleCopy = (content: string) => {
    copy(content)
    message.success(t('common.copySuccess'))
  }

  useEffect(() => {
    getApiList()
  }, [])
  /**
   * Fetch API key list for the application
   */
  const getApiList = () => {
    if (!application) {
      return
    }
    setApiKeyList([])
    getApiKeyList({
      type: application.type,
      is_active: true,
      resource_id: application.id,
      page: 1,
      pagesize: 10,
    }).then(res => {
      const response = res as { items: ApiKey[] }
      const list = response.items ?? []
      getAllStats([...list])
    })
  }
  /**
   * Fetch statistics for all API keys
   * @param list - List of API keys
   */
  const getAllStats = (list: ApiKey[]) => {
   const allList: ApiKey[] = []
   list.forEach(async item => {
      await getApiKeyStats(item.id)
        .then(res => {
          const response = res as { requests_today: number; total_requests: number; quota_limit: number; quota_used: number; }
          allList.push({
            ...item,
            ...response,
          })
          setApiKeyList(prev => [...prev, {
            ...item,
            ...response,
          }])
        })
    })

  }
  /**
   * Open modal to add new API key
   */
  const handleAdd = () => {
    apiKeyModalRef.current?.handleOpen()
  }
  /**
   * Open modal to edit API key
   * @param vo - API key to edit
   */
  const handleEdit = (vo: ApiKey) => {
    apiKeyConfigModalRef.current?.handleOpen(vo)
  }
  /**
   * Delete API key with confirmation
   * @param vo - API key to delete
   */
  const handleDelete = (vo: ApiKey) => {
      modal.confirm({
        title: t('common.confirmDeleteDesc', { name: vo.name }),
        content: t('application.apiKeyDeleteContent'),
        okText: t('common.delete'),
        cancelText: t('common.cancel'),
        okType: 'danger',
        onOk: () => {
          deleteApiKey(vo.id)
            .then(() => {
              getApiList();
              message.success(t('common.deleteSuccess'))
            })
        }
      })
  }

  // Calculate total requests across all API keys
  const totalRequests = apiKeyList.reduce((total, item) => total + item.total_requests, 0);
  return (
    <div className="rb:w-250 rb:mx-auto rb:max-h-[calc(100vh-88px)]! rb:overflow-y-auto">
      <Flex gap={20} vertical>
        <RbCard 
          title={() => (<Flex align="center">
            {t('application.endpointConfiguration')}
            <span className="rb:text-[#5B6167] rb:text-[12px]">({t('application.endpointConfigurationSubTitle')})</span>
          </Flex>)}
          headerType="borderless"
          headerClassName="rb:min-h-13.5!"
        >
          <Space size={8}>
            {['GET', 'POST', 'PUT', 'DELETE'].map((method) => (
              <div key={method} className={clsx("rb:w-20 rb:h-7 rb:leading-7 rb:text-center rb:rounded-md rb:text-regular", {
                'rb:bg-[#171719] rb:text-white': activeMethods.includes(method),
                'rb:bg-white rb:border rb:border-[#EBEBEB] rb:text-[#212332]': !activeMethods.includes(method),
              })}>
                {method}
              </div>
            ))}
          </Space>

          <Flex align="center" justify="space-between" className="rb:text-[#5B6167] rb:mt-4! rb:py-5! rb:px-4! rb:bg-white rb-border rb:rounded-lg rb:leading-5">
            {copyContent}
            
            <Button className="rb:px-2! rb:h-7! rb:group rb:-mt-1.75!" onClick={() => handleCopy(copyContent)}>
              <div 
                className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]" 
              ></div>
              {t('common.copy')}
            </Button>
          </Flex>
        </RbCard>
        <RbCard
          title={() => (<Flex align="center">
            {t('application.apiKeys')}
            <span className="rb:text-[#5B6167] rb:text-[12px]">({t('application.apiKeySubTitle')})</span>
          </Flex>)}
          extra={
            <Button style={{padding: '0 8px', height: '24px'}} onClick={handleAdd}>+ {t('application.addApiKey')}</Button>
          }
          headerType="borderless"
          headerClassName="rb:min-h-13.5!"
        >
          {/* Overview Data */}
          <Row className="rb:pl-1 rb:mb-4">
            <Col span={6}>
              <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[20px] rb:leading-7">{apiKeyList.length}</div>
              <div className="rb:mt-1 rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{t('application.apiKeyTotal')}</div>
            </Col>
            <Col span={6}>
              <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[20px] rb:leading-7">{totalRequests}</div>
              <div className="rb:mt-1 rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{t('application.apiKeyRequestTotal')}</div>
            </Col>
          </Row>
          {/* API Key List */}
          {apiKeyList.sort((a, b) => b.created_at - a.created_at).map(item => (
            <div key={item.id} className="rb:p-4 rb-border rb:rounded-xl">
              <Flex align="center" justify="space-between">
                <Flex vertical className="rb:max-w-[calc(100%-92px)]" gap={4}>
                  <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:flex-1 rb:leading-5 rb:font-medium">{item.name}</div>
                  <div className="rb:text-[#5B6167] rb:leading-4.5">ID: {item.id}</div>
                </Flex>
                <Space size={12}>
                  <div 
                    className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]" 
                    onClick={() => handleEdit(item)}
                  ></div>
                  <div 
                    className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]" 
                    onClick={() => handleDelete(item)}
                  ></div>
                </Space>
              </Flex>

              <Row className="rb:mt-4">
                <Col span={8}>
                  <Row className="rb:px-4 rb:py-2">
                    <Col span={12}>
                      <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5">{item.total_requests}</div>
                      <div className="rb:mt-1 rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{t('application.apiKeyRequestTotal')}</div>
                    </Col>
                    <Col span={12}>
                      <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5">{item.rate_limit}</div>
                      <div className="rb:mt-1 rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{t('application.qpsLimit')}</div>
                    </Col>
                  </Row>
                </Col>
                <Col span={16}>
                  <Flex align="center" justify="space-between" className="rb:text-[#5B6167] rb:py-5! rb:px-4! rb:bg-white rb-border rb:rounded-lg rb:leading-5">
                    {maskApiKeys(item.api_key)}

                    <Button className="rb:px-2! rb:h-7! rb:group rb:-mt-1.75!" onClick={() => handleCopy(item.api_key)}>
                      <div
                        className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
                      ></div>
                      {t('common.copy')}
                    </Button>
                  </Flex>
                </Col>
              </Row>
            </div>
          ))}
        </RbCard>
      </Flex>

      <ApiKeyModal
        ref={apiKeyModalRef}
        application={application}
        refresh={getApiList}
      />
      <ApiKeyConfigModal
        ref={apiKeyConfigModalRef}
        refresh={getApiList}
      />
    </div>
  );
}
export default Api;