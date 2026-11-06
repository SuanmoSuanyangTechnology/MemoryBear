import { forwardRef, useImperativeHandle, type ReactNode } from 'react';
import { Button, Empty, Flex, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';
import RbModal from '@/components/RbModal';
import type { ToolItem } from '../types';
import { useCustomToolDetail } from '../hooks/useCustomToolDetail';
import ToolIcon from './ToolIcon'

export interface CustomToolDetailModalRef {
  handleOpen: (item: ToolItem) => void;
}

interface CustomToolDetailModalProps {
  onEdit: (item: ToolItem) => void;
  onDelete: (item: ToolItem) => void;
  getStatusTag: (status: string) => ReactNode;
}

const CustomToolDetailModal = forwardRef<CustomToolDetailModalRef, CustomToolDetailModalProps>(({
  onEdit, onDelete, getStatusTag
}, ref) => {
    const { t } = useTranslation();
    const { visible, detail, methods, loading, open, close } = useCustomToolDetail();

    useImperativeHandle(ref, () => ({ handleOpen: open }));

    return (
      <RbModal
        open={visible}
        onCancel={close}
        title={detail
          ? (
            <Flex align="center" gap={16}>
              <ToolIcon className="rb:size-14!" iconClassName="rb:size-10!" iconUrl={detail.icon} />
              <div>
                <div className="rb:break-all rb:mb-1">{detail.name}</div>
                {getStatusTag(detail.status)}
              </div>
            </Flex>
          )
          : null
        }
        footer={detail
          ? [
            <Button key="edit" type="primary" onClick={() => { close(); onEdit(detail); }}>{t('common.edit')}</Button>,
            <Button key="delete" danger onClick={() => { close(); onDelete(detail); }}>{t('common.delete')}</Button>
          ]
          : null
        }
        width={640}
      >
        {detail && (
          <Flex vertical gap={8}>
            {detail.description &&
              <div className="rb:rounded-xl rb:bg-gray-100 rb:p-3 rb:text-gray-600 rb:whitespace-pre-wrap rb:break-all rb:mb-1">
                {detail.description || '-'}
              </div>
            }
            {loading ? (
              <Skeleton active paragraph={{ rows: 6 }} />
            ) : (
              <>
                <div className="rb:font-medium">
                  {t('tool.detailMethodCount', { count: methods.length })}
                </div>
                {methods.length === 0 && <Empty description={t('tool.noTools')} />}
                <div className="rb:grid rb:grid-cols-2 rb:gap-4 rb:max-h-96 rb:overflow-y-auto">
                  {methods.map(method => (
                    <div key={method.method_id} className="rb:rounded-xl rb:border rb:border-gray-200 rb:p-3 rb:break-all">
                      <div className="rb:font-semibold">{method.name}</div>
                      <div className="rb:mt-2 rb:text-gray-600 rb:text-[12px] rb:whitespace-pre-wrap">{method.description}</div>
                      <div className="rb:my-3 rb:border-t rb:border-gray-200" />
                      <div className="rb:mb-2 rb:font-semibold">{t('tool.inputParameters')}</div>
                      <Flex vertical gap={12} className="rb:text-[12px]">
                        {method.parameters.map(parameter => (
                          <div key={parameter.name}>
                            <span className="rb:font-mono">{parameter.name}</span>
                            <span className="rb:ml-2 rb:text-gray-500 rb:text-[11px]">
                              {parameter.type} · {t(parameter.required ? 'tool.parameterRequired' : 'tool.parameterOptional')}
                            </span>
                            <div className="rb:mt-2 rb:text-gray-600">{parameter.description}</div>
                          </div>
                        ))}
                      </Flex>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Flex>
        )}
      </RbModal>
    );
  },
);

CustomToolDetailModal.displayName = 'CustomToolDetailModal';
export default CustomToolDetailModal;
