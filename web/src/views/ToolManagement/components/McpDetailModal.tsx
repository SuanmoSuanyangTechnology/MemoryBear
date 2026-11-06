/**
 * McpDetailModal - MCP 服务详情弹窗
 * 
 * 展示 MCP 服务的基本信息、描述和工具列表，
 * 支持连接测试、编辑和删除操作。
 */
import { forwardRef, useImperativeHandle, useState, type ReactNode } from 'react';
import { Flex, Button, App, Tooltip, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolItem } from '../types';
import { getToolMethods, testConnection } from '@/api/tools';
import RbModal from '@/components/RbModal';
import Empty from '@/components/Empty'
import ToolIcon from './ToolIcon'

export interface McpDetailModalRef {
  handleOpen: (data: ToolItem) => void;
}

interface McpDetailModalProps {
  refresh?: () => void;
  onEdit?: (data: ToolItem) => void;
  onDelete?: (data: ToolItem) => void;
  getStatusTag: (status: string) => ReactNode;
}

interface ToolMethod {
  name: string;
  description: string;
}

const McpDetailModal = forwardRef<McpDetailModalRef, McpDetailModalProps>(({
  refresh, onEdit, onDelete, getStatusTag
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [currentTool, setCurrentTool] = useState<ToolItem | null>(null);
  const [methods, setMethods] = useState<ToolMethod[]>([]);

  useImperativeHandle(ref, () => ({
    handleOpen: (data: ToolItem) => {
      setCurrentTool(data);
      setOpen(true);
      fetchMethods(data.id);
    },
  }));

  const fetchMethods = (toolId: string) => {
    setLoading(true);
    getToolMethods(toolId)
      .then((res) => {
        setMethods(Array.isArray(res) ? res : []);
      })
      .catch(() => {
        setMethods([]);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  const handleTestConnection = () => {
    if (!currentTool?.id) return;
    testConnection(currentTool.id)
      .then(() => {
        message.success(t('tool.testConnectionSuccess'));
      })
      .finally(() => {
        refresh?.();
      });
  };

  const handleEdit = () => {
    if (currentTool) {
      onEdit?.(currentTool);
      setOpen(false);
    }
  };

  const handleDelete = () => {
    if (currentTool) {
      onDelete?.(currentTool);
      setOpen(false);
    }
  };

  const handleClose = () => {
    setOpen(false);
    setCurrentTool(null);
    setMethods([]);
  };

  const handleCopyUrl = () => {
    if (currentTool?.config_data?.server_url) {
      navigator.clipboard.writeText(currentTool.config_data.server_url)
        .then(() => {
          message.success(t('common.copySuccess'));
        });
    }
  };

  return (
    <RbModal
      open={open}
      onCancel={handleClose}
      title={currentTool
        ? (
          <Flex gap={16} align="flex-start">
            <ToolIcon className="rb:size-14!" iconClassName="rb:size-10!" iconUrl={currentTool.icon} />
            <Flex vertical gap={6} className="rb:flex-1 rb:min-w-0">
              <Flex align="center" gap={8}>
                <div className="rb:text-gray-900 rb:text-base rb:font-bold rb:leading-6">
                  {currentTool.name}
                </div>
                {getStatusTag(currentTool.status)}
              </Flex>
              <Flex align="center" gap={4} className="rb:text-gray-600 rb:text-sm rb:font-normal! rb:cursor-pointer rb:group/url rb:hover:text-primary-600">
                <Tooltip title={currentTool.config_data?.server_url}>
                  <span className="rb:break-all" onClick={handleCopyUrl}>
                    {currentTool.config_data?.server_url}
                  </span>
                </Tooltip>
                <div
                  className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/copy.svg')] rb:group-hover/url:bg-[url('@/assets/images/common/copy_blue.svg')]"
                  onClick={handleCopyUrl}
                />
              </Flex>
            </Flex>
          </Flex>
        )
        : null
      }
      footer={currentTool
        ? [
          <Button
            key="testLink"
            className="rb:group"
            onClick={handleTestConnection}
          >
            {t('tool.testLink')}
          </Button>,
          <Button key="edit" type="primary" onClick={handleEdit}>
            {t('common.edit')}
          </Button>,
          <Button key="delete" danger onClick={handleDelete}>
            {t('common.delete')}
          </Button>
        ]
        : null
      }
      width={640}
    >
      {currentTool && (
        <Flex vertical gap={8}>
          {currentTool.description && (
            <div className="rb:bg-gray-100 rb:rounded-lg rb:p-3 rb:mb-1 rb:text-gray-900 rb:text-sm rb:leading-5">
              {currentTool.description}
            </div>
          )}

          {loading
            ? (
              <Skeleton active paragraph={{ rows: 4 }} />
            )
            : methods.length > 0
            ? (
              <>
                <div className="rb:font-medium">
                  {methods.length}{t('tool.toolCount', { count: methods.length })}
                </div>
                <div className="rb:grid rb:grid-cols-2 rb:gap-3">
                  {methods.map((method) => (
                    <div key={method.name} className="rb:border rb:border-gray-200 rb:rounded-lg rb:p-3">
                      <div className="rb:text-gray-900 rb:text-sm rb:font-medium rb:mb-1 rb:break-all">
                        {method.name}
                      </div>
                      <div className="rb:text-gray-600 rb:text-xs rb:leading-4.5 rb:break-all">
                        {method.description}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )
            : (
              <Empty size={88} subTitle={t('tool.noTools')} />
            )
          }
        </Flex>
      )}
    </RbModal>
  );
});

McpDetailModal.displayName = 'McpDetailModal';

export default McpDetailModal;
