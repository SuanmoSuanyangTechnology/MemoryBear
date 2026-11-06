import { forwardRef, useImperativeHandle, useState, type ReactNode } from 'react';
import { Flex, Button, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import type { WorkflowToolItem, InputParameters } from '../types';
import { getToolDetail } from '@/api/tools';
import type { ToolDetail } from '../toolContract';
import Empty from '@/components/Empty'
import ToolIcon from './ToolIcon'

export interface WorkflowDetailModalRef {
  handleOpen: (item: WorkflowToolItem) => void;
}

interface WorkflowDetailModalProps {
  onEdit: (item: WorkflowToolItem) => void;
  onDelete: (item: WorkflowToolItem) => void;
  getStatusTag: (status: string) => ReactNode;
}

interface WorkflowDetail extends ToolDetail {
  workflow_config: {
    input_parameters: InputParameters[];
    output_schema: {
      type: string;
      properties: Record<string, { type: string }>;
    };
    timeout: number;
  };
}

const WorkflowDetailModal = forwardRef<WorkflowDetailModalRef, WorkflowDetailModalProps>(({
  onEdit, onDelete, getStatusTag
}, ref) => {
    const { t } = useTranslation();
    const [visible, setVisible] = useState(false);
    const [loading, setLoading] = useState(false);
    const [detail, setDetail] = useState<WorkflowDetail | null>(null);
    const [currentItem, setCurrentItem] = useState<WorkflowToolItem | null>(null);

    useImperativeHandle(ref, () => ({
      handleOpen: (item: WorkflowToolItem) => {
        setCurrentItem(item);
        setVisible(true);
        setLoading(true);
        setDetail(null);
        getToolDetail(item.id as string)
          .then((res) => {
            setDetail(res as WorkflowDetail);
          })
          .catch(() => {
            setDetail(null);
          })
          .finally(() => {
            setLoading(false);
          });
      },
    }));

    const handleClose = () => {
      setVisible(false);
      setDetail(null);
      setCurrentItem(null);
    };

    const handleEdit = () => {
      handleClose();
      if (currentItem) onEdit(currentItem);
    };

    const handleDelete = () => {
      handleClose();
      if (currentItem) onDelete(currentItem);
    };

    const inputParams = detail?.workflow_config?.input_parameters || [];

    return (
      <RbModal
        open={visible}
        onCancel={handleClose}
        title={currentItem
          ? (
            <Flex align="center" gap={16}>
              <ToolIcon className="rb:size-14!" iconClassName="rb:size-10!" iconUrl={currentItem.icon} />
              <div>
                <div className="rb:text-xl rb:font-semibold rb:mb-0.5">{currentItem?.name || detail?.name}</div>
                {getStatusTag(currentItem.status)}
              </div>
            </Flex>
          )
          : null
        }
        footer={[
          <Button key="edit" type="primary" onClick={handleEdit}>{t('common.edit')}</Button>,
          <Button key="delete" danger onClick={handleDelete}>{t('common.delete')}</Button>
        ]}
        width={640}
      >
        <Flex vertical gap={8}>
          {/* Description */}
          <div className="rb:rounded-lg rb:bg-gray-100 rb:p-3 rb:text-gray-600 rb:whitespace-pre-wrap rb:break-all rb:text-sm rb:leading-5 rb:mb-1">
            {currentItem?.description || '-'}
          </div>

          {/* Input Parameters */}
          {loading ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : (
            <>
              <div className="rb:font-medium">{t('tool.inputParams')}</div>
              {inputParams.length > 0
                ? (
                  <div className="rb:grid rb:grid-cols-2 rb:gap-3 rb:max-h-72 rb:overflow-y-auto">
                    {inputParams.map((param) => (
                      <div key={param.name} className="rb:rounded-lg rb:border rb:border-gray-200 rb:p-3 rb:break-all">
                        <div className="rb:font-semibold rb:text-gray-900">{param.name}</div>
                        <div className="rb:mt-1 rb:text-gray-500 rb:text-[12px]">
                          {param.type} · {t(param.required ? 'tool.parameterRequired' : 'tool.parameterOptional')}
                        </div>
                        {param.description && (
                          <div className="rb:mt-2 rb:text-gray-600 rb:text-sm">{param.description}</div>
                        )}
                      </div>
                    ))}
                  </div>
                )
                : (
                  <Empty size={88} subTitle={t('tool.noInputParams')} />
                )
              }
            </>
          )}
        </Flex>
      </RbModal>
    );
  },
);

WorkflowDetailModal.displayName = 'WorkflowDetailModal';
export default WorkflowDetailModal;
