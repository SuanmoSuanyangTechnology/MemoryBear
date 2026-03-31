/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:44:04 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 15:52:44
 */
/**
 * Prompt History Component
 * Displays saved prompts with view, edit, and delete actions
 */

import React, { useRef, type MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Space, App, Flex, Form } from 'antd';

import type { HistoryQuery, HistoryItem, PromptDetailRef } from '../types';
import RbCard from '@/components/RbCard/Card'
import { getPromptReleaseListUrl, deletePrompt } from '@/api/prompt'
import Markdown from '@/components/Markdown';
import { formatDateTime } from '@/utils/format'
import PromptDetail from '../components/PromptDetail'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import SearchInput from '@/components/SearchInput'
import Header from '../components/Header'

const History: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const scrollListRef = useRef<PageScrollListRef>(null)
  const detailRef = useRef<PromptDetailRef>(null)
  const { message, modal } = App.useApp()
  const [form] = Form.useForm<HistoryQuery>()
  const query = Form.useWatch([], form)

  /** View prompt details */
  const handleView = (item: HistoryItem) => {
    detailRef.current?.handleOpen(item)
  }
  /** Delete prompt */
  const handleDelete = (item: HistoryItem, e?: MouseEvent) => {
    e?.preventDefault();
    e?.stopPropagation();
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.title }),
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
  /** Edit prompt */
  const handleEdit = (item: HistoryItem) => {
    // edit(item)
    navigate('/prompt', {
      replace: true,
      state: { ...item }
    })
  }

  const handleClick = (key: string, item: HistoryItem) => {
    console.log('handleClick key', key)
    switch(key) {
      case 'detail':
        handleView(item)
        break
      case 'edit':
        handleEdit(item)
        break
      case 'delete':
        handleDelete(item)
        break
    }
  }

  return (
    <>
      <Flex justify="space-between" align="center" className="rb:mb-3!">
        <Header title={t('prompt.history')} desc={t('prompt.historyDesc')} />

        <Form form={form}>
          <Form.Item name="keyword" noStyle>
            <SearchInput
              placeholder={t('prompt.historySearchPlaceholder')}
              className="rb:w-75"
            />
          </Form.Item>
        </Form>
      </Flex>
      <PageScrollList<HistoryItem, HistoryQuery>
        ref={scrollListRef}
        url={getPromptReleaseListUrl}
        query={query}
        column={3}
        needLoading={false}
        renderItem={(item) => (
          <RbCard
            className="rb:cursor-pointer rb:relative"
            title={item.title}
            headerClassName='rb:min-h-[46px]!'
            headerType="borderless"
            bodyClassName="rb:px-3! rb:py-0!"
          >
            <div className="rb:h-35.5 rb:leading-5 rb:overflow-hidden rb:px-3 rb:py-2.5 rb:bg-[#F6F6F6] rb:rounded-lg">
              <Markdown content={item.prompt} className="rb:h-31! rb:overflow-hidden! rb:line-clamp-6! rb:break-word! rb:text-ellipsis!" />
            </div>
            
            <Flex align="center" justify="space-between" className="rb:py-3!">
              <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5">{formatDateTime(item.created_at, 'YYYY/MM/DD HH:mm')}</div>

              <Space size={8}>
                <div className="rb:size-4.5 rb:bg-cover rb:bg-[url('src/assets/images/prompt/eye.svg')] rb:hover:bg-[url('src/assets/images/prompt/eye_bg.svg')]"
                  onClick={() => handleClick('detail', item)}
                ></div>
                <div className="rb:size-4.5 rb:bg-cover rb:bg-[url('src/assets/images/prompt/edit.svg')] rb:hover:bg-[url('src/assets/images/prompt/edit_bg.svg')]"
                  onClick={() => handleClick('edit', item)}
                ></div>
                <div className="rb:size-4.5 rb:bg-cover rb:bg-[url('src/assets/images/prompt/delete.svg')] rb:hover:bg-[url('src/assets/images/prompt/delete_hover.svg')]"
                  onClick={() => handleClick('delete', item)}
                ></div>
              </Space>
            </Flex>
          </RbCard>
        )}
        heightClass="rb:h-[calc(100vh-126px)]!"
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