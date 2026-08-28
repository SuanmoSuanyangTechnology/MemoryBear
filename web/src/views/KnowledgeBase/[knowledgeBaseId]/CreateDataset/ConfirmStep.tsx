import { Button, Progress } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useTranslation } from 'react-i18next';
import Table, { type TableRef } from '@/components/Table';
import StatusTag from '@/components/StatusTag';
import type { KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';
import type { Ref } from 'react';

interface ConfirmStepProps {
  tableRef: Ref<TableRef>;
  knowledgeBaseId?: string;
  fileIds: string[];
  onDelete: (record: KnowledgeBaseDocumentData) => void;
}

const ConfirmStep = ({ tableRef, knowledgeBaseId, fileIds, onDelete }: ConfirmStepProps) => {
  const { t } = useTranslation();
  const columns: ColumnsType<KnowledgeBaseDocumentData> = [
    { title: t('knowledgeBase.name'), dataIndex: 'file_name', key: 'file_name' },
    {
      title: t('knowledgeBase.status'),
      dataIndex: 'progress',
      key: 'progress',
      render: (value: number | undefined, record) => {
        if (record.run === 0 && typeof value === 'number' && value < 0) {
          return <StatusTag status="error" text={t('knowledgeBase.failed')} />;
        }
        if (typeof value === 'number' && value >= 1) {
          return <StatusTag status="success" text={t('knowledgeBase.completed')} />;
        }
        if (typeof value === 'number' && value >= 0 && value < 1) {
          return (
            <Progress
              percent={Math.round(value * 100)}
              size="small"
              status="active"
              strokeColor={{ '0%': '#108ee9', '100%': '#87d068' }}
              className="rb:w-30!"
            />
          );
        }
        return <StatusTag status="warning" text={t('knowledgeBase.pending')} />;
      },
    },
    {
      title: t('common.operation'),
      key: 'action',
      render: (_, record) => <Button type="text" danger onClick={() => onDelete(record)}>{t('common.delete')}</Button>,
    },
  ];

  return (
    <div className="rb:text-sm rb:text-gray-500 rb:h-full! rb:overflow-y-auto rb:px-6 rb:pt-6">
      {knowledgeBaseId && fileIds.length > 0 ? (
        <Table<KnowledgeBaseDocumentData>
          ref={tableRef}
          apiUrl={`/documents/${knowledgeBaseId}/documents`}
          apiParams={{ document_ids: fileIds.join(',') }}
          columns={columns}
          rowKey="id"
          fillHeight={true}
        />
      ) : (
        <Table<KnowledgeBaseDocumentData>
          ref={tableRef}
          columns={columns}
          rowKey="id"
          initialData={[]}
          fillHeight={true}
        />
      )}
    </div>
  );
};

export default ConfirmStep;
