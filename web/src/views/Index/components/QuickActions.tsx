import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex } from 'antd'

interface QuickAction {
  key: string;
  iconClass: string;
  title: string;
  onClick?: () => void;
}

interface QuickActionsProps {
  className?: string;
  onNavigate?: (path: string) => void;
}

const QuickActions: FC<QuickActionsProps> = ({ onNavigate }) => {
  const { t } = useTranslation();

  const quickActions: QuickAction[] = [
    {
      key: 'model-management',
      iconClass: "rb:bg-[url('@/assets/images/index/model_mgt.svg')]",
      title: t('quickActions.modelManagement'),
      onClick: () => onNavigate?.('/model')
    },
    {
      key: 'space-management',
      iconClass: "rb:bg-[url('@/assets/images/index/space_mgt.svg')]",
      title: t('quickActions.spaceManagement'),
      onClick: () => onNavigate?.('/space')
    },
    {
      key: 'user-management',
      iconClass: "rb:bg-[url('@/assets/images/index/user_mgt.svg')]",
      title: t('quickActions.userManagement'),
      onClick: () => onNavigate?.('/user-management')
    },
  ];

  return (
    <div className='rb:w-full rb:bg-white rb:rounded-xl rb:py-3'>
      <div className='rb:font-[MiSans-Bold] rb:font-bold rb:leading-5 rb:px-4 rb:pb-3.5'>
        { t('quickActions.title') }
      </div>
      <div className="rb:grid rb:grid-cols-3 md:rb:grid-cols-4 rb:gap-4">
        {quickActions.map((action) => (
          <Flex
            key={action.key}
            vertical
            gap={8}
            align="center"
            justify="center"
            className="rb:cursor-pointer"
            onClick={action.onClick}
          >
            <div className={`rb:size-10 rb:mx-auto ${action.iconClass}`}></div>
            <div className="rb:text-[12px] rb:max-w-18.25 rb:text-[#5B6167] rb:text-center rb:leading-3.5">
              {action.title}
            </div>
          </Flex>
        ))}
      </div>
    </div>
  );
};

export default QuickActions;
