import { useState, useRef, useEffect, forwardRef, useImperativeHandle, type ReactNode } from 'react';
import {
  Row,
  Col,
  App,
  Space,
  Flex,
  Tooltip,
  Dropdown,
} from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolItem, CustomToolModalRef, CustomRef } from './types';
import CustomToolModal from './components/CustomToolModal';
import BodyWrapper from '@/components/Empty/BodyWrapper'
import RbCard from '@/components/RbCard'
import { getTools, deleteTool } from '@/api/tools'
import { formatDateTime } from '@/utils/format'

const Custom = forwardRef<CustomRef, { getStatusTag: (status: string) => ReactNode; keyword?: string | undefined }>(({ getStatusTag, keyword }, ref) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp()
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ToolItem[]>([]);
  const customToolModalRef = useRef<CustomToolModalRef>(null);

  useEffect(() => {
    getData()
  }, [keyword])

  const getData = () => {
    setLoading(true)
    getTools({
      tool_type: 'custom',
      name: keyword
    })
      .then((res) => {
        setData(res as ToolItem[])
      })
      .finally(() => {
        setLoading(false)
      })
  }

  // 打开添加服务弹窗
  const handleEdit = (data?: ToolItem) => {
    customToolModalRef.current?.handleOpen(data);
  };

  useImperativeHandle(ref, () => ({ handleEdit }));

  // 删除服务
  const handleDeleteService = (item: ToolItem) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteTool(item.id).then(() => {
          message.success(t('common.deleteSuccess'));
          getData()
        })
      }
    })
  };

  return (
    <>
      <BodyWrapper loading={loading} empty={data.length === 0}>
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
                            icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]" />,
                            label: t('common.edit'),
                            onClick: () => handleEdit(item),
                          },
                          {
                            key: 'delete',
                            className: 'rb:text-[#FF5D34]!',
                            icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                            label: t('common.delete'),
                            onClick: () => handleDeleteService(item),
                          },
                        ]
                      }}
                      placement="bottomRight"
                    >
                      <div className="rb:cursor-pointer rb:size-5.5 rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"></div>
                    </Dropdown>
                  </Flex>
                }
                isNeedTooltip={false}
              >
                {item.tags?.length > 0
                  ? <Flex gap={8} wrap align="center">
                    <Flex gap={6}>
                      {item.tags?.slice(0, 2).map((type, i) => (
                        <div key={i} className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">{type}</div>
                      ))}
                    </Flex>
                    {item.tags.length > 2 && (
                      <Tooltip
                        title={<Flex wrap gap={6}>{item.tags?.slice(2, item.tags.length).map((type, i) => (
                          <div key={i} className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5 rb:text-[#171719]">{type}</div>
                        ))}</Flex>}
                        color="white"
                        placement="bottom"
                      >
                        <div className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">+{item.tags.length - 2}</div>
                      </Tooltip>
                    )}
                  </Flex>
                  : <div className="rb:text-[#A8A9AA] rb:leading-5">{t('tool.noTags')}</div>
                }
                <Row className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2! rb:px-3! rb:leading-5 rb:mt-4!">
                  <Col span={12}>
                    <div className="rb:text-[#5B6167] rb:mb-1">{t('tool.auth_type')}</div>
                    {(item.config_data as any)?.auth_type}
                  </Col>
                  <Col span={12}>
                    <div className="rb:text-[#5B6167] rb:mb-1">{t('tool.created_at')}</div>
                    {formatDateTime(item.created_at)}
                  </Col>
                </Row>
              </RbCard>
            </Col>
          ))}
        </Row>
      </BodyWrapper>

      {/* 添加服务弹窗组件 */}
      <CustomToolModal 
        ref={customToolModalRef}
        refresh={getData} 
      />
    </>
  );
});

export default Custom;