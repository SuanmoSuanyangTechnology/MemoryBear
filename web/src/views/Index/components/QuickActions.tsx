import { type FC } from 'react';
import { useTranslation } from 'react-i18next';

import modelIcon from '@/assets/images/index/model_mgt.svg'
import spaceIcon from '@/assets/images/index/space_mgt.svg'
import workflowIcon from '@/assets/images/index/workflow_mgt.svg'
import userIcon from '@/assets/images/index/user_mgt.svg'
import dataExportIcon from '@/assets/images/index/data_export.svg'
import logIcon from '@/assets/images/index/log_mgt.svg'
import noteIcon from '@/assets/images/index/note_mgt.svg'
import helpCenterIcon from '@/assets/images/index/help_center.svg'
interface QuickAction {
  key: string;
  icon: string;
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
      icon: modelIcon,
      title: t('quickActions.modelManagement'),
      onClick: () => onNavigate?.('/model-management')
    },
    {
      key: 'space-management',
      icon: spaceIcon,
      title: t('quickActions.spaceManagement'),
      onClick: () => onNavigate?.('/spce')
    },
    {
      key: 'workflow-orchestration', 
      icon: workflowIcon,
      title: t('quickActions.workflowOrchestration'),
      onClick: () => onNavigate?.('/workflow')
    },
    {
      key: 'user-management',
      icon: userIcon,
      title: t('quickActions.userManagement'),
      onClick: () => onNavigate?.('/user-management')
    },
    {
      key: 'data-export',
      icon: dataExportIcon,
      title: t('quickActions.dataExport'),
      onClick: () => onNavigate?.('/')
    },
    {
      key: 'log-query',
      icon: logIcon,
      title: t('quickActions.logQuery'),
      onClick: () => onNavigate?.('/log')
    },
    {
      key: 'notification-reminder', 
      icon: noteIcon,
      title: t('quickActions.notificationReminder'),
      onClick: () => onNavigate?.('/notification-reminder')
    },

    {
      key: 'help-center',
      icon: helpCenterIcon,
      title: t('quickActions.helpCenter'),
      onClick: () => onNavigate?.('/help-center')
    }
  ];

  return (
    <div className='rb:w-full rb:p-4 rb:bg-[#FBFDFF] rb:border-1 rb:border-[#DFE4ED] rb:rounded-xl'>
        <div className='rb:flex rb:justify-start rb:text-base rb:font-medium rb:text-[#212332]'>
            { t('quickActions.title') }
        </div>
        <div className="rb:grid rb:grid-cols-3 md:rb:grid-cols-4 rb:gap-4 rb:mt-4">
            
            {quickActions.map((action) => (
                <div key={action.key}
                className="rb:flex rb:flex-col rb:items-center rb:text-center rb:cursor-pointer rb:group"
                onClick={action.onClick}
                >
                <img src={action.icon} className='rb:size-10 rb:mx-auto' />
                <div className="rb:mt-2 rb:text-xs rb:max-w-[74px] rb:text-[#5B6167] rb:text-center rb:leading-[14px]">
                    {action.title}
                </div>
                </div>
            ))}
        </div>
  </div>);
};

export default QuickActions;
