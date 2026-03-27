import { useState, useRef, useEffect, forwardRef, useImperativeHandle, type ReactNode } from 'react';
import {
  App,
  Space,
  Tooltip,
  Dropdown,
  Flex,
  Row, Col,
} from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolItem, McpServiceModalRef, McpRef } from './types';
import McpServiceModal from './components/McpServiceModal';
import BodyWrapper from '@/components/Empty/BodyWrapper'
import RbCard from '@/components/RbCard'
import { getTools, deleteTool, testConnection } from '@/api/tools'
import { formatDateTime } from '@/utils/format'

const Mcp = forwardRef<McpRef, { getStatusTag: (status: string) => ReactNode; keyword?: string | undefined }>(({ getStatusTag, keyword }, ref) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp()
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ToolItem[]>([]);
  const addServiceModalRef = useRef<McpServiceModalRef>(null);

  useEffect(() => {
    getData()
  }, [keyword])

  const getData = () => {
    setLoading(true)
    getTools({
      tool_type: 'mcp',
      name: keyword
    })
      .then((res) => {
        setData(res as ToolItem[])
      })
      .finally(() => {
        setLoading(false)
      })
  }

  useImperativeHandle(ref, () => ({ handleEdit, getData }));

  // 打开添加服务弹窗
  const handleEdit = (data?: ToolItem) => {
    addServiceModalRef.current?.handleOpen(data);
  };

  // 测试连接
  const handleTestConnection = (item: ToolItem) => {
    if (!item.id) {
      return
    }
    testConnection(item.id)
      .then(() => {
        message.success(t('tool.testConnectionSuccess'));
      })
      .finally(() => {
        getData()
      })
  };
  // 删除服务
  const handleDeleteService = (item: ToolItem) => {
    if (!item.id) {
      return
    }
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteTool(item.id as string)
          .then(() => {
            message.success(t('common.deleteSuccess'));
            getData()
          })
      }
    })
  };

  return (
    <>
      <BodyWrapper loading={loading} empty={data?.length === 0}>
        <Row
          gutter={[16, 16]}
          className="rb:max-h-[calc(100%-48px)] rb:overflow-y-auto"
        >
          {data.map((item) => (
            <Col span={8} key={item.id}>
              <RbCard
                title={
                  <Flex justify="space-between" gap={16}>
                    <Space size={8} className="rb:flex-1!">
                      <Tooltip title={item.name}>
                        <div className="rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                      </Tooltip>
                      {getStatusTag(item.status)}
                    </Space>
                    <Dropdown
                      menu={{
                        items: [
                          {
                            key: 'edit',
                            icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit.svg')]" />,
                            label: t('common.edit'),
                            onClick: () => handleEdit(item),
                          },
                          {
                            key: 'link',
                            icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/conversation/link.svg')]" />,
                            label: t('tool.testLink'),
                            onClick: () => handleTestConnection(item),
                          },
                          {
                            key: 'delete',
                            className: 'rb:text-[#FF5D34]!',
                            icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red.svg')]" />,
                            label: t('common.delete'),
                            onClick: () => handleDeleteService(item),
                          },
                        ]
                      }}
                      placement="bottomRight"
                    >
                      <div className="rb:cursor-pointer rb:size-6 rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"></div>
                    </Dropdown>
                  </Flex>
                }
                isNeedTooltip={false}
              >
                <Flex vertical gap={4} className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2! rb:px-3! rb:text-[#5B6167] rb:leading-5">
                  {t(`tool.server_url`)}
                  <div className="rb:h-10 rb:break-all rb:line-clamp-2 rb:text-[#171719]">
                    {item.config_data?.server_url}
                  </div>
                </Flex>
                <div className="rb:text-[#5B6167] rb:leading-4.5 rb:text-[12px] rb:mt-4">{t('tool.last_health_check')}: {formatDateTime(item.config_data?.last_health_check)}</div>

              </RbCard>
            </Col>
          ))}
          <Col span={8}></Col>
        </Row>
      </BodyWrapper>

      {/* 添加服务弹窗组件 */}
      <McpServiceModal 
        ref={addServiceModalRef}
        refresh={getData} 
      />
    </>
  );
});

export default Mcp;