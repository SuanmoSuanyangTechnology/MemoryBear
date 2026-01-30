import React, { useRef, type MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Tooltip, Space, App } from 'antd';
import { EyeOutlined } from '@ant-design/icons';

import type { HistoryQuery, HistoryItem, PromptDetailRef } from './types';
import RbCard from '@/components/RbCard/Card'
import { getPromptReleaseListUrl, deletePrompt } from '@/api/prompt'
import Markdown from '@/components/Markdown';
import { formatDateTime } from '@/utils/format'
import PromptDetail from './components/PromptDetail'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'

const History: React.FC<{ query: HistoryQuery; edit: (item: HistoryItem) => void; }> = ({ query, edit }) => {
  const { t } = useTranslation();
  const scrollListRef = useRef<PageScrollListRef>(null)
  const detailRef = useRef<PromptDetailRef>(null)
  const { message, modal } = App.useApp()

  const handleView = (item: HistoryItem) => {
    detailRef.current?.handleOpen(item)
  }
  const handleDelete = (item: HistoryItem, e?: MouseEvent) => {
    e?.preventDefault();
    e?.stopPropagation();
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.title }),
      content: t('application.apiKeyDeleteContent'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deletePrompt(item.id).then(() => {
          message.success(t('common.deleteSuccess'))
          scrollListRef.current?.refresh()
          detailRef.current?.handleClose()
        })
      }
    })

  }
  const handleEdit = (item: HistoryItem) => {
    edit(item)
  }

  return (
    <>
      <PageScrollList
        ref={scrollListRef}
        url={getPromptReleaseListUrl}
        query={query}
        column={3}
        renderItem={(item) => {
          const historyItem = item as unknown as HistoryItem;
          return (
            <RbCard
              className="rb:cursor-pointer"
              headerType="borderless"
              bodyClassName="rb:p-4!"
              title={<Tooltip title={historyItem.title}>{historyItem.title}</Tooltip>}
              extra={<div className="rb:text-[12px] rb:text-[#5B6167]">{formatDateTime(historyItem.created_at, 'YYYY/MM/DD HH:mm')}</div>}
              onClick={() => handleView(historyItem)}
            >
              <div className="rb:text-[12px] rb:h-30 rb:overflow-hidden rb:px-3 rb:py-2.5 rb:bg-[#F6F8FC] rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:shadow-[0px_4px_8px_0px_rgba(33,35,50,0.12)]">
                <Markdown content={historyItem.prompt} className="rb:h-full! rb:overflow-y-auto" />
              </div>

              <div className="rb:mt-4 rb:text-[12px] rb:leading-4 rb:font-regular rb:text-[#5B6167] rb:flex rb:items-center rb:justify-end">
                <Space size={16}>
                  <EyeOutlined className="rb:text-[16px]" onClick={() => handleView(historyItem)} />
                  <div
                    className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]"
                    onClick={() => handleEdit(historyItem)}
                  ></div>
                  <div
                    className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]"
                    onClick={(e) => handleDelete(historyItem, e)}
                  ></div>
                </Space>
              </div>
            </RbCard>
          );
        }}
      />

      <PromptDetail
        ref={detailRef}
        handleEdit={handleEdit}
        handleDelete={handleDelete}
      />
    </>
  );
};

export default History;