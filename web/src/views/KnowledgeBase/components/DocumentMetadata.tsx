/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-05 13:33:10 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-17 17:20:34
 */
import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Input, Space, App, Form, InputNumber, DatePicker, Tooltip, Flex } from 'antd';

import Empty from '@/components/Empty';
import type { MetadataField } from '../types';
import { getDocumentMetadata, updateDocumentMetadata, deleteDocumentMetadata } from '@/api/knowledgeBase';
import MetadataFieldSelectorModal, { type MetadataFieldSelectorModalRef } from './MetadataFieldSelectorModal';
import dayjs from 'dayjs';
import { formatDateTime } from '@/utils/format'

interface DocumentMetadataProps {
  documentId: string;
  knowledgeBaseId: string;
}

const DocumentMetadata: React.FC<DocumentMetadataProps> = ({ documentId, knowledgeBaseId }) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [documentMetadataFields, setDocumentMetadataFields] = useState<MetadataField[]>([]);
  const [documentMetadata, setDocumentMetadata] = useState<Record<string, any>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const metadataModalRef = useRef<MetadataFieldSelectorModalRef>(null);

  const getCurrentMetadata = () => {
    setLoading(true);
    getDocumentMetadata(documentId)
      .then((res) => {
        const { metadata, fields } = res as {
          metadata: Record<string, any>;
          fields: MetadataField[];
        }
        const newMetadata: Record<string, any> = {}
        Object.keys(metadata || {}).forEach(key => {
          const filterField = fields.find(item => item.name === key);
          if (filterField && filterField.type === 'time') {
            newMetadata[key] = metadata[key] ? formatDateTime(metadata[key], 'YYYY-MM-DD HH:mm:ssZZ') : null;
          } else {
            newMetadata[key] = metadata[key];
          }
        })
        setDocumentMetadataFields(fields || []);
        setDocumentMetadata(newMetadata || {});
      })
      .finally(() => {
        setLoading(false);
      });
  };

  useEffect(() => {
    getCurrentMetadata();
  }, [documentId, knowledgeBaseId]);

  const handleStartEdit = () => {
    setIsEditing(true);
    const values: Record<string, any> = {}
    documentMetadataFields.map((item: MetadataField) => {
      if (item.type === 'time') {
        values[item.name] = item.value ? dayjs.tz(item.value) : undefined;
      } else {
        values[item.name] = item.value;
      }
    })
    form.setFieldsValue(values);
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    getCurrentMetadata();
  };

  const handleAddMetadata = () => {
    metadataModalRef.current?.open();
  };

  const handleRefreshMetadataFields = (fields: MetadataField[]) => {
    const newFields = [...documentMetadataFields];
    fields.forEach(field => {
      if (!newFields.find(item => item.name === field.name)) {
        newFields.push({ ...field, value: '' });
      }
    });
    setDocumentMetadataFields(newFields);
  };

  const handleDeleteMetadata = (fieldName: string) => {
    deleteDocumentMetadata(documentId, {
      field_names: [fieldName]
    })
      .then(() => {
        setDocumentMetadataFields(prev => prev.filter(item => item.name !== fieldName));
      })
  };

  const handleSave = async () => {
      const values = form.getFieldsValue();

      Object.keys(values).forEach(key => {
        if (typeof values[key] === 'object') {
          values[key] = values[key].format('YYYY-MM-DD HH:mm:ssZZ');
        }
      });

      setIsSaving(true);
      updateDocumentMetadata(documentId, { metadata: values })
        .then(() => {
          message.success(t('common.saveSuccess'));
          getCurrentMetadata();
          setIsEditing(false);
        })
        .finally(() => {
          setIsSaving(false);
        });
  };

  const renderFieldValue = (field: MetadataField) => {
    
    if (isEditing) {
      switch (field.type) {
        case 'string':
          return (
            <Form.Item name={field.name} className="rb:mb-0!">
              <Input
                placeholder={t('knowledgeBase.enterMetadataValue')}
              />
            </Form.Item>
          );
        case 'number':
          return (
            <Form.Item name={field.name} className="rb:mb-0!">
              <InputNumber
                placeholder={t('knowledgeBase.enterMetadataValue')}
                style={{ width: '100%' }}
              />
            </Form.Item>
          );
        case 'time':
          return (
            <Form.Item name={field.name} className="rb:mb-0!">
              <DatePicker
                showTime
                format="YYYY-MM-DD HH:mm:ssZZ"
              />
            </Form.Item>
          );
        default:
          return (
            <Form.Item name={field.name} className="rb:mb-0!">
              <Input
                placeholder={t('knowledgeBase.enterMetadataValue')}
              />
            </Form.Item>
          );
      }
    } else {
      // 非编辑状态，只显示值
      return <span className="rb:text-sm rb:text-gray-600">{documentMetadata[field.name] ?? '-'}</span>;
    }
  };

  return (
    <div className="rb:mb-4">
      <Flex align="center" justify="space-between" className="rb:mb-3!">
        <Flex align="center" gap={8}>
          <span className="rb:font-medium rb:text-lg">{t('knowledgeBase.metadata.label')}</span>
          <Tooltip title={t('knowledgeBase.metadata.description')}>
            <div className="rb:size-4.5 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>
          </Tooltip>
        </Flex>
        {isEditing ? (
          <Space>
            <Button onClick={handleCancelEdit} disabled={isSaving}>
              {t('common.cancel')}
            </Button>
            <Button type="primary" onClick={handleSave} disabled={isSaving} loading={isSaving}>
              {t('common.save')}
            </Button>
          </Space>
        ) : (
          <Button
            type="text"
            onClick={handleStartEdit}
          >
            {t('common.edit')}
          </Button>
        )}
      </Flex>

      {loading ? (
        <div className="rb:py-4 rb:text-center rb:text-gray-500">{t('common.loading')}</div>
      ) : (
        <Form form={form} layout="horizontal">
          <>
            {isEditing && (
              <div className="rb:space-y-2 rb:mb-4">
                <Button
                  type="dashed"
                  block
                  icon={
                    <span
                      className="rb:inline-block rb:w-4 rb:h-4 rb:bg-cover"
                      style={{
                        backgroundImage: `url('@/assets/images/common/plus_dark.svg')`,
                      }}
                    />
                  }
                  onClick={handleAddMetadata}
                >
                  {t('knowledgeBase.metadata.add')}
                </Button>
              </div>
            )}

            {documentMetadataFields.length > 0 && (
              <div className="rb:space-y-3">
                {documentMetadataFields.map(field => (
                  <Flex key={field.id} align="center" gap={12}>
                    <span className="rb:text-sm rb:text-gray-700 rb:w-17.5">{field.name}</span>
                    <div className="rb:flex-1">
                      {renderFieldValue(field)}
                    </div>
                    {isEditing && (
                      <div
                        className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                        onClick={() => handleDeleteMetadata(field.name)}
                      ></div>
                    )}
                  </Flex>
                ))}
              </div>
            )}

          {documentMetadataFields.length === 0 && !isEditing && (
            <Empty size={88} />
          )}
          </>
        </Form>
      )}

     {/* 多选添加元数据弹窗 */}
      <MetadataFieldSelectorModal
        ref={metadataModalRef}
        knowledgeBaseId={knowledgeBaseId}
        refresh={handleRefreshMetadataFields}
      />
    </div>
  );
};

export default DocumentMetadata;
