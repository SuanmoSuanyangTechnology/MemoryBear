/*
 * Debug chat panel
 * Reuses the Chat component, supporting attachment input and streaming AI replies
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import Chat from '@/components/Chat'
import Empty from '@/components/Empty'
import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import ChatToolbar, { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { ChatItem } from '@/components/Chat/types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import { chatFeatures } from './constants'
import { formatDateTime } from '@/utils/format'

interface DebugChatProps {
  chatList: ChatItem[];
  chatLoading: boolean;
  streamLoading: boolean;
  message: string;
  fileList: any[];
  setFileList: (list: any[]) => void;
  toolbarRef: React.RefObject<ChatToolbarRef>;
  onChange: (msg: string) => void;
  onSend: (msg?: string) => void;
}

const DebugChat: FC<DebugChatProps> = ({
  chatList,
  chatLoading,
  streamLoading,
  message,
  fileList,
  setFileList,
  toolbarRef,
  onChange,
  onSend,
}) => {
  const { t } = useTranslation()

  return (
    <div className="rb-border rb:rounded-xl rb:pt-3 rb:mb-4 rb:h-75">
      <Chat
        data={chatList}
        loading={chatLoading}
        streamLoading={streamLoading}
        message={message}
        labelPosition="bottom"
        labelFormat={(item) => formatDateTime(item.created_at)}
        empty={
          <Empty url={ConversationEmptyIcon} className="rb:h-full!" size={[140, 100]} title={t('memoryExtractionEngine.chatEmpty')} isNeedSubTitle={false} />
        }
        contentClassName={clsx('rb:px-4', {
          'rb:h-[calc(100%-140px)]': !fileList.length,
          'rb:h-[calc(100%-208px)]': !!fileList.length,
        })}
        fileList={fileList}
        fileChange={(list) => {
          setFileList(list || [])
          toolbarRef.current?.setFiles(list || [])
        }}
        onChange={onChange}
        onSend={onSend}
      >
        <ChatToolbar
          ref={toolbarRef}
          features={chatFeatures as FeaturesConfigForm}
          onFilesChange={setFileList}
        />
      </Chat>
    </div>
  )
}
export default DebugChat
