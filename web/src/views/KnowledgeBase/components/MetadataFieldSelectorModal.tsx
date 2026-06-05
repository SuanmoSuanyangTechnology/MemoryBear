/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-05 13:33:21 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-06-05 13:33:21 
 */
import { useState, useEffect, forwardRef, useImperativeHandle, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Checkbox, Spin, Button } from 'antd';
import { useNavigate } from 'react-router-dom';

import RbModal from '@/components/RbModal';
import type { MetadataField, MetadataModalRef } from '../types';
import { getMetadataFields } from '@/api/knowledgeBase';
import MetadataModal from '../components/MetadataModal';

interface MetadataFieldSelectorModalProps {
  knowledgeBaseId: string;
  refresh: (fields: MetadataField[]) => void;
}

export interface MetadataFieldSelectorModalRef {
  open: () => void;
  close: () => void;
}

const MetadataFieldSelectorModal = forwardRef<MetadataFieldSelectorModalRef, MetadataFieldSelectorModalProps>(({
  knowledgeBaseId,
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [visible, setVisible] = useState(false);
  const [metadataFields, setMetadataFields] = useState<MetadataField[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedFields, setSelectedFields] = useState<MetadataField[]>([]);
  const metadataModalRef = useRef<MetadataModalRef>(null);

  useImperativeHandle(ref, () => ({
    open: () => {
      setVisible(true);
    },
    close: () => {
      handleClose();
    }
  }));

  useEffect(() => {
    if (visible && knowledgeBaseId) {
      fetchMetadataFields();
    }
  }, [visible, knowledgeBaseId]);

  const fetchMetadataFields = async () => {
    setLoading(true);
    getMetadataFields(knowledgeBaseId)
      .then(res => {
        const response = res as { custom: MetadataField[] };
        setMetadataFields(response.custom || []);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  const handleClose = () => {
    setVisible(false);
    setSelectedFields([]);
  };

  const handleSave = () => {
    refresh(selectedFields);
    handleClose();
  };

  const handleSelect = (field: MetadataField) => {
    if (selectedFields.find(item => item.name === field.name)) {
      setSelectedFields(selectedFields.filter(item => item.name !== field.name));
    } else {
      setSelectedFields([...selectedFields, field]);
    }
  };

  // Handle metadata
  const handleMetadata = () => {
    if (!knowledgeBaseId) return;
    metadataModalRef?.current?.handleOpen(knowledgeBaseId);
  }
  const handleManage = () => {
    navigate(-1)
  }

  return (
    <>
      <RbModal
        title={t('knowledgeBase.metadata.add')}
        open={visible}
        onCancel={handleClose}
        footer={[
          <Button key="add" type="link" onClick={handleMetadata}>+ {t('knowledgeBase.metadata.add')}</Button>,
          <Button key="manage" type="text" onClick={handleManage}>{t('knowledgeBase.metadata.manage')}</Button>,
          <Button key="cancel" onClick={handleClose}>{t('common.cancel')}</Button>,
          <Button key="confirm" type="primary" onClick={handleSave}>{t('common.confirm')}</Button>,
        ]}
      >
        {loading ? (
          <div className="rb:py-8 rb:text-center">
            <Spin />
          </div>
        ) : (
          <>
            {metadataFields.map(field => (
              <div
                key={field.id}
                className="rb:flex rb:items-center rb:gap-3 rb:cursor-pointer rb:p-2 rb:rounded-lg rb:hover:bg-[#F9F9F9]"
                onClick={() => handleSelect(field)}
              >
                <Checkbox
                  checked={!!selectedFields.find(item => item.name === field.name)}
                  onChange={() => handleSelect(field)}
                />
                <span className="rb:text-sm">{field.name}</span>
                <span className="rb:text-xs rb:text-gray-400 rb:ml-auto">{field.type}</span>
              </div>
            ))}
            {metadataFields.length === 0 && (
              <div className="rb:py-8 rb:text-center rb:text-gray-400">
                {t('knowledgeBase.metadata.noAvailableFields')}
              </div>
            )}
          </>
        )}
      </RbModal>

      <MetadataModal
        ref={metadataModalRef}
        refresh={fetchMetadataFields}
      />
    </>
  );
});

export default MetadataFieldSelectorModal;
