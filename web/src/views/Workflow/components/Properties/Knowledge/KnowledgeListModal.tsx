import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { List, Form, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'

import type { KnowledgeModalRef, KnowledgeBase } from './types'
import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types'
import RbModal from '@/components/RbModal'
import { getKnowledgeBaseList } from '@/api/knowledgeBase'
import SearchInput from '@/components/SearchInput'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format';
interface KnowledgeModalProps {
  refresh: (rows: KnowledgeBase[], type: 'knowledge') => void;
  selectedList: KnowledgeBase[];
}

const KnowledgeListModal = forwardRef<KnowledgeModalRef, KnowledgeModalProps>(({
  refresh,
  selectedList
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [list, setList] = useState<KnowledgeBaseListItem[]>([])
  const [filterList, setFilterList] = useState<KnowledgeBaseListItem[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [selectedRows, setSelectedRows] = useState<KnowledgeBase[]>([])

  const [form] = Form.useForm()
  const query = Form.useWatch([], form)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields()
    setSelectedIds([])
    setSelectedRows([])
  };

  const handleOpen = () => {
    setVisible(true);
    form.resetFields()
    setSelectedIds([])
    setSelectedRows([])
  };

  useEffect(() => {
    if (visible) {
      getList()
    }
  }, [query?.keywords, visible])
  const getList = () => {
    getKnowledgeBaseList(undefined, {
      ...query,
      pagesize: 100,
      orderby:'created_at',
      desc:true,
    })
      .then(res => {
        const response = res as { items: KnowledgeBaseListItem[] }
        setList(response.items || [])
      })
  }
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    refresh(selectedRows.map(item => ({
      ...item,
      config: {
        similarity_threshold: 0.7,
        retrieve_type: "hybrid",
        top_k: 3,
        weight: 1,
      }
    })), 'knowledge')
    setVisible(false);
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  const handleSelect = (item: KnowledgeBase) => {
    const index = selectedIds.indexOf(item.id)
    if (index === -1) {
      setSelectedIds([...selectedIds, item.id])
      setSelectedRows([...selectedRows, item])
    } else {
      setSelectedIds(selectedIds.filter(id => id !== item.id))
      setSelectedRows(selectedRows.filter(row => row.id !== item.id))
    }
  }

  useEffect(() => {
    if (list.length && selectedList.length) {
      const unSelectedList = list.filter(item => selectedList.findIndex(vo => vo.id === item.id) < 0)
      setFilterList([...unSelectedList])
    } else if (list.length) {
      setFilterList([...list])
    }
  }, [list, selectedList])

  return (
    <>
      <RbModal
        title={t('application.chooseKnowledge')}
        open={visible}
        onCancel={handleClose}
        okText={t('common.save')}
        onOk={handleSave}
        width={1000}
      >
        <Flex gap={24} vertical>
          <Form form={form}>
            <Form.Item name="keywords" noStyle>
              <SearchInput
                placeholder={t('knowledgeBase.searchPlaceholder')}
                className="rb:w-full!"
                variant="outlined"
              />
            </Form.Item>
          </Form>
          {filterList.length === 0 
            ? <Empty />
            : <List
              grid={{ gutter: 16, column: 2 }}
              dataSource={filterList}
              renderItem={(item: KnowledgeBase) => (
                <List.Item key={item.id}>
                  <Flex
                    align="center"
                    justify="space-between"
                    className={clsx("rb:border rb:rounded-lg rb:p-[17px_16px]! rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
                    "rb:bg-[rgba(21,94,239,0.06)] rb:border-[#171719] rb:text-[#171719]": selectedIds.includes(item.id),
                    "rb:border-[#DFE4ED] rb:text-[#212332]": !selectedIds.includes(item.id),
                  })} onClick={() => handleSelect(item)}>
                    <div className="rb:text-[16px] rb:leading-5.5">
                      {item.name}
                      <div className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167] rb:mt-2">{t('application.contains', {include_count: item.doc_num})}</div>
                    </div>
                    <div className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167]">{formatDateTime(item.created_at, 'YYYY-MM-DD HH:mm:ss')}</div>
                  </Flex>
                </List.Item>
              )}
            />
          }
        </Flex>
      </RbModal>
    </>
  );
});

export default KnowledgeListModal;