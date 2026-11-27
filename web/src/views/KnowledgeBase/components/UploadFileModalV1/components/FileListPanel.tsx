import { Button, Flex } from 'antd';
import { CloseOutlined, UploadOutlined } from '@ant-design/icons';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import Empty from '@/components/Empty';
import Tag from '@/components/Tag';
import { getFileIconClassByMimeType } from '@/utils/fileIcon';
import { formatFileSize } from '@/utils/format';
import type { SelectedFile } from '../types';

interface FileListPanelProps {
  fileList: SelectedFile[];
  isRechunkMode: boolean;
  onAddFiles: () => void;
  onRemove: (uid: string) => void;
}

/** Left column of the modal: shows the "Upload Document Confirmation" title and the selected file list. */
const FileListPanel = ({ fileList, isRechunkMode, onAddFiles, onRemove }: FileListPanelProps) => {
  const { t } = useTranslation();
  const panelTitle = isRechunkMode
    ? t('knowledgeBase.rechunking')
    : t('knowledgeBase.uploadModal.uploadConfirmDesc');

  return (
    <Flex
      vertical
      aria-label={panelTitle}
      className="rb:w-60 rb:h-full! rb:overflow-hidden! rb:shrink-0 rb:border-r rb:border-gray-200 rb:bg-gray-50"
    >
      <Flex align="center" justify="space-between" className="rb:h-15.5! rb:p-4! rb-border-b rb:shrink-0">
        <Flex align="center" gap={8}>
          <span className="rb:text-[16px] rb:font-medium">{panelTitle}</span>
          <Tag color="default" circle variant="borderless" className="rb:min-w-5.5! rb:text-center rb:text-[14px]! rb:h-5!">
            {fileList.length}
          </Tag>
        </Flex>
        {!isRechunkMode && (
          <Button
            type="text"
            size="small"
            icon={<UploadOutlined />}
            aria-label={t('knowledgeBase.uploadModal.addFile')}
            onClick={onAddFiles}
          />
        )}
      </Flex>

      <Flex vertical gap={8} className="rb:flex-1 rb:overflow-y-auto rb:p-3!">
        {fileList.map((item) => (
          <Flex
            key={item.uid}
            gap={8}
            className="rb:rounded-lg rb:px-2! rb:py-2! rb:hover:bg-[#F2F4F7]"
          >
            <div
              className={clsx(
                'rb:size-5 rb:shrink-0 rb:bg-cover rb:opacity-65',
                getFileIconClassByMimeType(item.originFile?.type, item.name),
              )}
            />
            <div className="rb:min-w-0 rb:flex-1">
              <div className="rb:truncate rb:text-[13px]" title={item.name}>
                {item.name}
              </div>
              <div className="rb:text-xs rb:text-gray-500">{formatFileSize(item.size)}</div>
            </div>
            {!isRechunkMode && (
              <Button
                type="text"
                size="small"
                icon={<CloseOutlined />}
                aria-label={t('knowledgeBase.uploadModal.removeFile', { name: item.name })}
                onClick={() => onRemove(item.uid)}
              />
            )}
          </Flex>
        ))}
        {fileList.length === 0 && (
          <Empty size={88} subTitle={t('knowledgeBase.uploadModal.noFiles')} className="rb:h-full!" />
        )}
      </Flex>
    </Flex>
  );
};

export default FileListPanel;
