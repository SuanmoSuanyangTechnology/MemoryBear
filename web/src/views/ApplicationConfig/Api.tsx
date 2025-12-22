import { type FC, useState, useRef, useEffect } from 'react';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { Button, Space, App, Statistic, Row, Col } from 'antd';
import copy from 'copy-to-clipboard'

import Card from './components/Card';
import type { Application } from '@/views/ApplicationManagement/types'
import type { ApiKeyModalRef, ApiKeyConfigModalRef } from './types'
import type { ApiKey } from '@/views/ApiKeyManagement/types'
import ApiKeyModal from './components/ApiKeyModal';
import ApiKeyConfigModal from './components/ApiKeyConfigModal';
import Tag from '@/components/Tag'
import { getApiKeyList, getApiKeyStats, deleteApiKey } from '@/api/apiKey';
import { maskApiKeys } from '@/utils/apiKeyReplacer'

const Api: FC<{ application: Application | null }> = ({ application }) => {
  const { t } = useTranslation();
  const activeMethods = ['GET'];
  const { message, modal } = App.useApp()
  const copyContent = window.location.origin + '/v1/chat'
  const apiKeyModalRef = useRef<ApiKeyModalRef>(null);
  const apiKeyConfigModalRef = useRef<ApiKeyConfigModalRef>(null);
  const [apiKeyList, setApiKeyList] = useState<ApiKey[]>([])

  const handleCopy = (content: string) => {
    copy(content)
    message.success(t('common.copySuccess'))
  }

  useEffect(() => {
    getApiList()
  }, [])
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
  const handleAdd = () => {
    apiKeyModalRef.current?.handleOpen()
  }
  const handleEdit = (vo: ApiKey) => {
    apiKeyConfigModalRef.current?.handleOpen(vo)
  }
  const handleDelete = (vo: ApiKey) => {
      modal.confirm({
        title: t('common.confirmDeleteDesc', { name: vo.name }),
        content: t('application.apiKeyDeleteContent'),
        okText: t('common.delete'),
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

  // 计算total_requests总数
  const totalRequests = apiKeyList.reduce((total, item) => total + item.total_requests, 0);
  return (
    <div className="rb:w-250 rb:mt-5 rb:pb-5 rb:mx-auto">
      <Space size={20} direction="vertical" style={{width: '100%'}}>
        <Card 
          title={t('application.endpointConfiguration')}
        >
          <div className="rb:text-[#5B6167] rb:text-[12px] rb:mb-2">{t('application.endpointConfigurationSubTitle')}</div>
          <div className="rb:p-[20px_20px_24px_20px] rb:bg-[#F0F3F8] rb:border rb:border-[#DFE4ED] rb:rounded-lg">
            <Space size={8}>
              {['GET', 'POST', 'PUT', 'DELETE'].map((method) => (
                <div key={method} className={clsx("rb:w-20 rb:h-7 rb:leading-7 rb:text-center rb:rounded-md rb:text-regular", {
                  'rb:bg-[#155EEF] rb:text-white': activeMethods.includes(method),
                  'rb:bg-white': !activeMethods.includes(method),
                })}>
                  {method}
                </div>
              ))}
            </Space>

            <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:mt-5 rb:p-[20px_16px] rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:leading-5">
              {copyContent}
              
              <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(copyContent)}>
                <div 
                  className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]" 
                ></div>
                {t('common.copy')}
              </Button>
            </div>
          </div>
        </Card>
        <Card
          title={t('application.apiKeys')}
          extra={
            <Button style={{padding: '0 8px', height: '24px'}} onClick={handleAdd}>+ {t('application.addApiKey')}</Button>
          }
        >
          <div className="rb:text-[#5B6167] rb:text-[12px] rb:mb-2">{t('application.apiKeySubTitle')}</div>
          {/* 总览数据 */}
          <Row>
            <Col span={6}>
              <Statistic title={t('application.apiKeyTotal')} value={apiKeyList.length} />
            </Col>
            <Col span={6}>
              <Statistic title={t('application.apiKeyRequestTotal')} value={totalRequests} />
            </Col>
          </Row>
          {/* API Key 列表 */}
          {apiKeyList.sort((a, b) => b.created_at - a.created_at).map(item => (
            <div key={item.id} className="rb:mt-4 rb:p-[10px_12px] rb:bg-[#F0F3F8] rb:border rb:border-[#DFE4ED] rb:rounded-lg">
              <div className="rb:flex rb:items-center rb:justify-between">
                <div className="rb:flex rb:items-center rb:max-w-[calc(100%-92px)]">
                  <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:flex-1">{item.name}</div>
                  <Tag className="rb:ml-2">ID: {item.id}</Tag>
                </div>
                <Space>
                  <div 
                    className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]" 
                    onClick={() => handleEdit(item)}
                  ></div>
                  <div 
                    className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]" 
                    onClick={() => handleDelete(item)}
                  ></div>
                </Space>
              </div>
              <div className="rb:mb-3 rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:mt-5 rb:p-[8px_16px] rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:leading-5">
                {maskApiKeys(item.api_key)}
                
                <Button className="rb:px-2! rb:h-7! rb:group" onClick={() => handleCopy(item.api_key)}>
                  <div 
                    className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]" 
                  ></div>
                  {t('common.copy')}
                </Button>
              </div>
 
              <Row gutter={12}>
                <Col span={8}>
                  <Statistic valueStyle={{ fontSize: '18px' }} title={t('application.apiKeyRequestTotal')} value={item.total_requests} />
                </Col>
                <Col span={8}>
                  <Statistic valueStyle={{ fontSize: '18px' }} title={t('application.qpsLimit')} value={item.rate_limit} />
                </Col>
              </Row>
            </div>
          ))}
        </Card>
      </Space>

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