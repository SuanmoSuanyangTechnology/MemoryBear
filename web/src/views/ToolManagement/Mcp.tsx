import { useRef, forwardRef, useImperativeHandle } from 'react';
import { Flex, Row, Col, Tooltip, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolItem, McpServiceModalRef, McpRef } from './types';
import McpServiceModal from './components/McpServiceModal';
import McpDetailModal, { type McpDetailModalRef } from './components/McpDetailModal';
import BodyWrapper from '@/components/Empty/BodyWrapper';
import RbCard from '@/components/RbCard';
import MoreDropdown from '@/components/MoreDropdown';
import { testConnection } from '@/api/tools';
import { useToolList } from './hooks/useToolList';
import { useToolStatus } from './hooks/useToolStatus';
import { useToolDelete } from './hooks/useToolDelete';
import ToolIcon from '@/assets/images/tool/tool.svg';

const Mcp = forwardRef<McpRef, { keyword?: string | undefined }>(({ keyword }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const getStatusTag = useToolStatus();
  const { loading, data, getData } = useToolList<ToolItem>({ toolType: 'mcp', keyword });
  const handleDelete = useToolDelete(getData);

  const addServiceModalRef = useRef<McpServiceModalRef>(null);
  const detailModalRef = useRef<McpDetailModalRef>(null);

  const handleEdit = (data?: ToolItem) => {
    addServiceModalRef.current?.handleOpen(data);
  };

  useImperativeHandle(ref, () => ({ handleEdit, getData }));

  const handleViewDetail = (item: ToolItem) => {
    detailModalRef.current?.handleOpen(item);
  };

  const handleTestConnection = (item: ToolItem) => {
    if (!item.id) return;
    testConnection(item.id)
      .then(() => {
        message.success(t('tool.testConnectionSuccess'));
      })
      .finally(() => {
        getData();
      });
  };

  return (
    <>
      <BodyWrapper loading={loading} empty={data?.length === 0}>
        <Row gutter={[16, 16]}>
          {data.map((item) => (
            <Col span={8} key={item.id}>
              <RbCard
                isNeedTooltip={false}
                avatarText={
                  <img
                    src={ToolIcon}
                    alt=""
                    className="rb:size-7"
                    style={{ filter: 'brightness(0) invert(1)' }}
                  />
                }
                avatarClassName="rb:bg-[#4da8ff]!"
                title={
                  <div className="rb:flex-1!">
                    <div className="rb:leading-5.5 rb:wrap-break-word rb:line-clamp-1">
                      {item.name}
                    </div>
                    {item.description && (
                      <Tooltip title={item.description}>
                        <div className="rb:text-gray-600 rb:text-[12px] rb:font-normal! rb:leading-5 rb:wrap-break-word rb:line-clamp-1">
                          {item.description}
                        </div>
                      </Tooltip>
                    )}
                  </div>
                }
                extra={<div className="rb:ml-2">{getStatusTag(item.status)}</div>}
                className="rb:group/card rb:cursor-pointer"
                onClick={() => handleViewDetail(item)}
              >
                <Flex align="center" justify="space-between" gap={8}>
                  <span className="rb:text-[12px] rb:text-gray-600">
                    {item.parameters?.length} 个工具
                  </span>
                  <MoreDropdown
                    items={[
                      {
                        key: 'edit',
                        icon: (
                          <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]" />
                        ),
                        label: t('common.edit'),
                        onClick: () => handleEdit(item),
                      },
                      {
                        key: 'link',
                        icon: (
                          <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/link.svg')]" />
                        ),
                        label: t('tool.testLink'),
                        onClick: () => handleTestConnection(item),
                      },
                      {
                        key: 'delete',
                        className: 'rb:text-[#FF5D34]!',
                        icon: (
                          <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />
                        ),
                        label: t('common.delete'),
                        onClick: () => handleDelete(item),
                      },
                    ]}
                  />
                </Flex>
              </RbCard>
            </Col>
          ))}
          <Col span={8}></Col>
        </Row>
      </BodyWrapper>

      <McpServiceModal ref={addServiceModalRef} refresh={getData} />
      <McpDetailModal
        ref={detailModalRef}
        refresh={getData}
        onEdit={handleEdit}
        onDelete={handleDelete}
        getStatusTag={getStatusTag}
      />
    </>
  );
});

Mcp.displayName = 'Mcp';

export default Mcp;
