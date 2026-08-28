import { Flex, Form, Input } from 'antd';
import type { UploadFile } from 'antd';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import type { Ref } from 'react';
import { useTranslation } from 'react-i18next';
import UploadFiles, { type UploadFilesRef } from '@/components/Upload/UploadFiles';
import type { SourceType } from './types';

const fileTypes = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'md', 'htm', 'html', 'json', 'ppt', 'pptx', 'txt', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'mp3', 'mp4', 'mov', 'wav'];
const csvFileTypes = ['csv'];

interface SourceStepProps {
  source: SourceType;
  uploadRef: Ref<UploadFilesRef>;
  onUpload: (options: UploadRequestOption) => void;
  onRemove: (file: UploadFile) => Promise<boolean>;
  onFileListChange: (fileList: UploadFile[]) => void;
  fileList: UploadFile[];
}

const SourceStep = ({ source, uploadRef, onUpload, onRemove, onFileListChange, fileList }: SourceStepProps) => {
  const { t } = useTranslation();

  return (
    <div className="rb:w-full rb:pb-6!">
      <Flex className="rb:w-full rb:p-6!">
        {(source === 'local' || source === 'csv') && (
          <UploadFiles
            ref={uploadRef}
            isCanDrag
            fileSize={100}
            multiple={source !== 'csv'}
            maxCount={source === 'csv' ? 1 : 99}
            fileType={source === 'csv' ? csvFileTypes : fileTypes}
            customRequest={onUpload}
            onChange={onFileListChange}
            onRemove={onRemove}
            fileList={fileList}
          />
        )}
        {source === 'link' && (
          <Flex vertical className="rb:w-full rb:mt-10! rb:px-40!">
            <div className="rb:text-sm rb:font-medium rb:text-gray-800 rb:mb-3">{t('knowledgeBase.webLink')}</div>
            <Input.TextArea rows={6} placeholder={t('knowledgeBase.webLinkPlaceholder')} />
            <div className="rb:text-sm rb:text-gray-500 rb:mt-3">{t('knowledgeBase.webLinkDesc', { count: 5 })}</div>
            <div className="rb:text-sm rb:font-medium rb:text-gray-800 rb:mt-10 rb:mb-3">{t('knowledgeBase.selectorTutorial')}</div>
            <Input placeholder={t('knowledgeBase.webLinkPlaceholder')} />
          </Flex>
        )}
        {source === 'text' && (
          <Flex vertical className="rb:w-full rb:px-20!">
            <Form.Item name="title" label={t('knowledgeBase.title')} rules={[{ required: true, whitespace: true, message: t('knowledgeBase.pleaseEnterTitle') }]}>
              <Input placeholder={t('knowledgeBase.pleaseEnterTitle')} />
            </Form.Item>
            <Form.Item name="content" label={t('knowledgeBase.customContent')} rules={[{ required: true, whitespace: true, message: t('knowledgeBase.pleaseEnterContent') }]}>
              <Input.TextArea placeholder={t('knowledgeBase.pleaseEnterContent')} rows={8} showCount maxLength={5000} />
            </Form.Item>
          </Flex>
        )}
      </Flex>
      {source === 'csv' && (
        <a href="csv_template.csv" download="csv_template.csv" className="rb:mx-6 rb:text-sm rb:font-medium rb:text-gray-800 rb:-mt-6!">
          {t('knowledgeBase.csvTemplate')}
        </a>
      )}
    </div>
  );
};

export default SourceStep;
