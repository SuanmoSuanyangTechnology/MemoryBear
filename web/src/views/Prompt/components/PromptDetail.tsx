import { forwardRef, useImperativeHandle, useState } from 'react';
import { Flex, Button, App } from 'antd';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'

import type { HistoryItem, PromptDetailRef } from '../types'
import RbModal from '@/components/RbModal'
import Markdown from '@/components/Markdown';
import { formatDateTime } from '@/utils/format'

const PromptDetail = forwardRef<PromptDetailRef, { handleEdit: (item: HistoryItem) => void; handleDelete: (item: HistoryItem) => void; }>(({ handleEdit, handleDelete }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [data, setData] = useState<HistoryItem>({} as HistoryItem)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
  };

  const handleOpen = (vo: HistoryItem) => {
    setVisible(true);
    setData(vo)
  };
    const handleCopy = (text = '') => {
      copy(text)
      message.success(t('common.copySuccess'))
    }

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  return (
    <RbModal
      title={<div>
        {data.title}
        <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-normal rb:mt-1!">{formatDateTime(data.created_at)}</div>
      </div>}
      open={visible}
      footer={
        <Flex justify="end" gap={8}>
          <Button danger onClick={() => handleDelete(data)}>{t('common.delete')}</Button>
          <Button type="primary" onClick={() => {
            handleClose()
            handleEdit(data)
          }}>{t('common.edit')}</Button>
        </Flex>
      }
      onCancel={handleClose}
      width={1000}
    >
      <Flex justify="space-between">
        {t('prompt.initialInput')}
        <Button className="rb:group" size="small" disabled={!data.first_message || data.first_message.trim() === ''} onClick={() => handleCopy(data.first_message)}>
          <div
            className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
          ></div>
        </Button>
      </Flex>

      <div className="rb:my-3 rb:bg-[#F6F8FC] rb:border-[#DFE4ED] rb:rounded-lg rb:p-3">
        <Markdown content={data.first_message} className="rb:min-h-5 rb:max-h-50 rb:overflow-y-auto" />
      </div>

      <Flex justify="space-between">
        {t('prompt.conversationOptimizationPrompt')}
        <Button className="rb:group" size="small" onClick={() => handleCopy(data.prompt)}>
          <div
            className="rb:w-4 rb:h-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
          ></div>
        </Button>
      </Flex>
      <div className="rb:relative rb:my-3 rb:overflow-hidden rb:bg-[#F6F8FC] rb:border-[#DFE4ED] rb:rounded-lg rb:p-3">
        <Markdown content={data.prompt} className="rb:min-h-5 rb:max-h-70 rb:overflow-y-auto" />
      </div>
    </RbModal>
  );
});

export default PromptDetail;