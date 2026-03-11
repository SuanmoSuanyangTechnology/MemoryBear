/**
 * SharedReadonlyView
 * 使用共享模式下的只读视图，只展示试运行对话界面，不暴露内部配置
 */
import { type FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Application } from '@/views/ApplicationManagement/types';
import Chat from './components/Chat';
import type { ChatData, Config } from './types';

interface SharedReadonlyViewProps {
  application: Application;
}

const SharedReadonlyView: FC<SharedReadonlyViewProps> = ({ application }) => {
  const { t } = useTranslation();
  // 初始化时注入 app_id，供 Chat 内部调用 draftRun/runCompare 使用
  const [chatList, setChatList] = useState<ChatData[]>([{ list: [] }]);

  // 构造最小 Config，只需 app_id 字段
  const chatData: Config = { app_id: application.id } as unknown as Config;

  return (
    <div className="rb:flex rb:h-[calc(100vh-64px)] rb:flex-col rb:items-center rb:justify-center rb:bg-[#F5F7FA]">
      <div className="rb:w-full rb:max-w-[760px] rb:h-full rb:flex rb:flex-col rb:bg-white rb:rounded-xl rb:shadow-sm rb:overflow-hidden">
        <div className="rb:px-6 rb:py-4 rb:border-b rb:border-[#DFE4ED] rb:flex rb:items-center rb:gap-3">
          <div className="rb:w-8 rb:h-8 rb:rounded-lg rb:bg-[#155EEF] rb:flex rb:items-center rb:justify-center rb:text-white rb:text-base rb:font-medium">
            {application.name?.[0]}
          </div>
          <div>
            <div className="rb:text-sm rb:font-medium rb:text-gray-800">{application.name}</div>
            <div className="rb:text-xs rb:text-gray-400">{t('appShare.readonlyViewTip')}</div>
          </div>
        </div>
        <div className="rb:flex-1 rb:overflow-hidden">
          <Chat
            chatList={chatList}
            data={chatData}
            updateChatList={setChatList}
            handleSave={async () => {}}
            source="multi_agent"
          />
        </div>
      </div>
    </div>
  );
};

export default SharedReadonlyView;
