/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-20 14:27:18 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-20 14:27:18 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { UploadFile } from 'antd';
import { useParams } from 'react-router-dom';

import RbModal from '@/components/RbModal';
import UploadFiles from '@/components/Upload/UploadFiles';
import { importAnnotation } from '@/api/application';
import { useI18n } from '@/store/locale'

const BatchImportModal = forwardRef<{ handleOpen: () => void; handleClose: () => void }, { refresh: () => void; }>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { id } = useParams();
  const { language } = useI18n()
  const [visible, setVisible] = useState(false);
  const [fileList, setFileList] = useState<File[]>([]);
  const [loading, setLoading] = useState(false);

  const handleClose = () => {
    setFileList([]);
    setVisible(false);
    setLoading(false);
  };

  const handleOpen = () => {
    setVisible(true);
  };

  const handleFileChange = (fileList: UploadFile[]) => {
    setFileList(fileList as unknown as File[]);
  };

  const handleImport = () => {
    if (!fileList.length || !id) return;
    const formData = new FormData();
    formData.append('file', fileList[0]);
    setLoading(true);
    importAnnotation(id, formData)
      .then(() => {
        handleClose();
        refresh()
      })
      .finally(() => setLoading(false))
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.batchImport')}
      open={visible}
      onCancel={handleClose}
      okText={t('application.confirmImport')}
      onOk={handleImport}
      confirmLoading={loading}
      okButtonProps={{ disabled: !fileList.length || !id }}
      width={520}
    >
      <UploadFiles
        isAutoUpload={false}
        isCanDrag={true} 
        fileSize={100} 
        multiple={false} 
        maxCount={1}
        fileType={['csv']}
        onChange={handleFileChange}
      />

      {/* CSV Structure Info */}
      <div className="rb:mb-6">
        <p className="rb:text-sm rb:text-[#646A73] rb:mb-3">{t('application.csvStructure')}</p>
        
        {/* Table structure example */}
        <div className="rb-border rb:rounded-lg rb:overflow-hidden">
          <div className="rb:flex rb:bg-[#F5F7FA] rb:text-[#646A73] rb:text-sm">
            <div className="rb:flex-1 rb:px-4 rb:py-2 rb-border-r">问题</div>
            <div className="rb:flex-1 rb:px-4 rb:py-2">回答</div>
          </div>
          <div className="rb:flex rb:text-[#212332] rb:text-sm">
            <div className="rb:flex-1 rb:px-4 rb:py-2 rb-border-r rb-border-t">问题 1</div>
            <div className="rb:flex-1 rb:px-4 rb:py-2 rb-border-t">回答 1</div>
          </div>
          <div className="rb:flex rb:text-[#212332] rb:text-sm">
            <div className="rb:flex-1 rb:px-4 rb:py-2 rb-border-r">问题 2</div>
            <div className="rb:flex-1 rb:px-4 rb:py-2">回答 2</div>
          </div>
        </div>
      </div>

      {/* Download Template */}
      <div className="rb:mb-6">
        <a
          href={`annotations-template-${language}.csv`}
          download="annotations-template.csv"
          className='rb:text-sm rb:font-medium rb:text-gray-800 rb:-mt-6!'
        >
          {t('application.downloadTemplate')}
        </a>
      </div>
    </RbModal>
  );
});

export default BatchImportModal;
