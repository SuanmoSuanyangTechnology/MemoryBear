/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-24 16:31:24 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-24 16:31:24 
 */
import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Flex, Button, Empty, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';

import type { LogDetailModalRef, LogItem } from '../types'
import RbModal from '@/components/RbModal'
import { getAppLogDetail } from '@/api/application'
import ChatContent from '@/components/Chat/ChatContent'
import { formatDateTime } from '@/utils/format'
import type { ChatItem } from '@/components/Chat/types'

/** Log detail data with conversation messages */
type Data = LogItem & {
  messages: ChatItem[];
}

/** Modal component for displaying conversation log details */
const LogDetailModal = forwardRef<LogDetailModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [vo, setVo] = useState<LogItem | null>(null)
  const [data, setData] = useState<Data>({} as Data)

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
    setVo(null)
    setData({} as Data)
  };

  /** Open modal */
  const handleOpen = (item: LogItem) => {
    setVisible(true);
    setVo(item)
  };

  /** Fetch detail when modal opens */
  useEffect(() => {
    if (visible && vo) {
      getDetail()
    }
  }, [visible, vo])

  /** Fetch conversation log detail from API */
  const getDetail = () => {
    if (!vo) return
    setLoading(true)
    getAppLogDetail(vo.app_id, vo.id).then(res => {
      setData(res as Data)
    })
    .finally(() => {
      setLoading(false)
    })
  }
  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={<>
        {data.title}
        <div className="rb:text-[#5B6167] rb:leading-4.5 rb:text-[12px]">{formatDateTime(data.created_at, 'YYYY.MM')} - {formatDateTime(data.updated_at, 'YYYY.MM')}</div>
      </>}
      open={visible}
      onCancel={handleClose}
      footer={null}
      width={1000}
    >
      <Flex justify="space-between" align="center" className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2.5! rb:pr-2.5! rb:pl-3.25!">
        {t('workingDetail.conversationStream')}
        <Button className="rb:h-6!" onClick={getDetail}>{t('workingDetail.refresh')}</Button>
      </Flex>
      <div className="rb-border rb:p-3 rb:rounded-xl rb:mt-3 rb:h-116.5 rb:overflow-y-auto">
      {loading
        ? <Skeleton active />
        : data.messages?.length === 0
          ? <Empty className="rb:my-20" />
          : (
            <ChatContent
              contentClassNames="rb:max-w-110!"
              data={data.messages || []}
              streamLoading={false}
              labelFormat={(item) => formatDateTime(item.created_at)}
            />
          )
      }
      </div>
    </RbModal>
  );
});

export default LogDetailModal;