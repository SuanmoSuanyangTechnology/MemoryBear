import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Button, App, Flex } from 'antd';
import { ExclamationCircleFilled } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'
import { useParams } from 'react-router-dom'
import dayjs from 'dayjs'
import html2canvas from 'html2canvas'

import type {  ShareModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { generateShareLink } from '@/api/application'
import RbAlert from '@/components/RbAlert'
import ChatContent from '@/components/Chat/ChatContent';
import type { ChatItem } from '@/components/Chat/types';

/**
 * Component props
 */
interface ShareModalProps {
  /** Version to share */
  conversationId: string | null;
  chatList: Array<ChatItem | ChatItem[]>;
  streamLoading: boolean;
}

/**
 * Modal for sharing application versions
 */
const ShareModal = forwardRef<ShareModalRef, ShareModalProps>(({
  conversationId,
  chatList,
  streamLoading
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const { token } = useParams()
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [shareLink, setShareLink] = useState<string | null>(null)
  const chatContentRef = useRef<HTMLDivElement>(null)

  /** Close modal */
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
  };

  /** Open modal and generate share link */
  const handleOpen = () => {
    if (!conversationId || !token) {
      return
    }
    setLoading(true)
    generateShareLink(token, conversationId, { allow_copy: true })
      .then(res => {
        const response = res as { share_uuid: string }
        if (response?.share_uuid) {
          setShareLink(`${window.location.origin}/#/share-chat/${response?.share_uuid}`)
          setVisible(true);
        }
      })
      .finally(() => {
        setLoading(false)
      })
  };
  /** Copy share link to clipboard */
  const handleCopy = () => {
    if (!shareLink) return
    copy(shareLink)
    message.success(t('common.copySuccess'))
  }
  const exportAsImage = async () => {
    const container = chatContentRef.current
    if (!container) return

    const canvas = await html2canvas(container, {
      backgroundColor: '#ffffff',
      scale: 2,
      useCORS: true,
    })

    const link = document.createElement('a')
    link.download = `chat-${Date.now()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('memoryConversation.shareConversation')}
      open={visible}
      onCancel={handleClose}
      footer={false}
    >
      <>
        <div className="rb:leading-5 rb:mb-2 rb:font-medium">{t('memoryConversation.shareLink')}</div>
        <Flex align="center" justify="space-between" wrap={false} gap={10} className="rb:mb-3!">
          <div
            className="rb:flex-1 rb:overflow-hidden rb:whitespace-nowrap rb:text-ellipsis rb:cursor-pointer rb:h-8 rb:p-[6px_10px] rb:bg-[#FFFFFF] rb:border rb:border-[#EBEBEB] rb:rounded-md rb:leading-5"
            onClick={handleCopy}
          >
            {shareLink}
          </div>

          <Button type="primary" loading={loading} disabled={!shareLink} onClick={handleCopy}>
            {t('common.copy')}
          </Button>
        </Flex>
        <Flex align="center" wrap={false} gap={10} className="rb:leading-5 rb:my-4! rb:font-medium">
          {t('memoryConversation.shareImage')}

          <Button type="primary" loading={loading} onClick={exportAsImage}>
            {t('memoryConversation.generateShareImage')}
          </Button>
        </Flex>
        <RbAlert color="orange" icon={<ExclamationCircleFilled />}>
          {t('memoryConversation.shareLinkTip')}
        </RbAlert>
      </>

      <div ref={chatContentRef} className="rb:fixed rb:-z-1000">
        <ChatContent
          classNames="rb:p-3"
          data={chatList}
          streamLoading={streamLoading}
          labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
        />
      </div>
    </RbModal>
  );
});

export default ShareModal;