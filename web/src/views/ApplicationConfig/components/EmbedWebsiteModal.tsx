/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:46 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-15 14:28:08
 */
/**
 * Release Share Modal
 * Generates and displays a shareable link for a specific application version
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'

import type { Release, EmbedWebsiteModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { shareRelease } from '@/api/application'
import CodeBlock from '@/components/Markdown/CodeBlock'

/**
 * Component props
 */
interface EmbedWebsiteModalProps {
  /** Version to share */
  version: Release | null
}

const types = ['iframe', 'scripts']

const scriptsContent = `<script>
 window.redbearaiChatbotConfig = {
  token: {{share_token}},
  baseUrl: {{origin}}
 }
</script>
<script
 src="{{origin}}/embed.min.js"
 id="{{share_token}}"
 defer>
</script>
<style>
  #chatbot-bubble-button {
    background-color: #1C64F2 !important;
  }
  #chatbot-bubble-window {
    width: 24rem !important;
    height: 40rem !important;
  }
</style>`
const iframeContent = `<iframe
 src="{{origin}}/#/chat-box/{{share_token}}"
 style="width: 100%; height: 100%; min-height: 700px"
 frameborder="0"
 allow="microphone">
</iframe>`
const contents = {
  iframe: iframeContent,
  scripts: scriptsContent
}
/**
 * Modal for sharing application versions
 */
const EmbedWebsiteModal = forwardRef<EmbedWebsiteModalRef, EmbedWebsiteModalProps>(({
  version
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [allContents, setAllContents] = useState<Record<string, string>>( contents)

  /** Close modal */
  const handleClose = () => {
    setVisible(false);
  };

  /** Open modal and generate share link */
  const handleOpen = () => {
    if (!version) {
      return
    }
    shareRelease(version?.app_id, version.id || '')
      .then(res => {
        const response = res as { share_token: string }
        if (response?.share_token) {
          setVisible(true);
          setAllContents({
            iframe: contents.iframe.replace(/{{origin}}/g, window.location.origin).replace(/{{share_token}}/g, response?.share_token || ''),
            scripts: contents.scripts.replace(/{{origin}}/g, window.location.origin).replace(/{{share_token}}/g, response?.share_token || ''),
          })
        }
      })
  };

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const [type, setType] = useState(types[0])

  
  console.log('allContents', allContents, allContents[type])
  return (
    <RbModal
      title={t('application.embedWebsite')}
      open={visible}
      onCancel={handleClose}
      footer={false}
      width={800}
    >
      <>
        <div className="rb:leading-5 rb:mb-2 rb:font-medium">{t('application.embedWebsiteTip')}</div>
        <Flex gap={10}>
          <div
            className={clsx("rb:cursor-pointer rb:border rb:border-transparent rb:rounded-lg rb:bg-cover rb:w-47 rb:h-32 rb:bg-[url('@/assets/images/conversation/iframe-option.svg')]", {
              'rb:border-[#171719]!': type === 'iframe'
            })}
            onClick={() => setType('iframe')}
          ></div>
          <div
            className={clsx("rb:cursor-pointer rb:border rb:border-transparent rb:rounded-lg rb:bg-cover rb:w-47 rb:h-32 rb:bg-[url('@/assets/images/conversation/scripts-option.svg')]", {
              'rb:border-[#171719]!': type === 'scripts'
            })}
            onClick={() => setType('scripts')}
          ></div>
        </Flex>
        <div className="rb:leading-5 rb:mt-4 rb:mb-2 rb:font-medium">{t('application.embedWebsiteCodeTip')}</div>
        <CodeBlock
          showLineNumbers={true}
          background="#EBEBEB"
          value={allContents[type]}
        />
      </>
    </RbModal>
  );
});

export default EmbedWebsiteModal;