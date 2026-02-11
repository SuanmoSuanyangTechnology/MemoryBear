/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:16:38 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:16:38 
 */
/**
 * Quick Operation Component
 * Displays shortcut cards for common operations
 * Includes navigation to application, knowledge base, memory conversation, and help center
 */

import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom';

import Card from './Card';
import applicationIcon from '@/assets/images/menu/application_active.svg';
import knowledgeIcon from '@/assets/images/menu/knowledge_active.svg';
import memoryConversationIcon from '@/assets/images/menu/memoryConversation_active.svg';
import helpCenterIcon from '@/assets/images/menu/helpCenter_active.svg'
import arrowTopRight from '@/assets/images/home/arrow_top_right.svg';

/** Quick operation items configuration */
const quickOperations = [
  { key: 'createNewApplication', url: '/application' },
  { key: 'createNewKnowledge', url: '/knowledge-base' },
  { key: 'memoryConversation', url: '/memory-conversation' },
  { key: 'helpCenter', url: '' },
]

/** Icon mapping for quick operations */
const quickOperationIcons: {[key: string]: string | undefined} = {
  createNewApplication: applicationIcon,
  createNewKnowledge: knowledgeIcon,
  memoryConversation: memoryConversationIcon,
  helpCenter: helpCenterIcon
}
const QuickOperation:FC = () => {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate();

  /** Handle navigation or external link */
  const handleJump = (url: string | null) => {
    if (url) {
      navigate(url)
    }else{
      const currentLang = i18n.language;
      const lang = currentLang === 'zh' ? 'zh' : 'en';
      const helpUrl = `https://docs.redbearai.com/s/${lang}-memorybear`;
      
      /** Create hidden link to avoid popup blocking */
      const link = document.createElement('a');
      link.href = helpUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }
  }
  return (
    <Card
      title={t('dashboard.quickOperation')}
    >
      <div className="rb:grid rb:grid-cols-4 rb:gap-4">
        {quickOperations.map(item => (
          <div key={item.key} className="rb:rounded-lg rb:p-[20px_16px] rb:border rb:border-[#DFE4ED] rb:cursor-pointer rb:hover:border-[#155EEF]" onClick={() => handleJump(item.url)}>
            <div className="rb:flex rb:justify-between">
              <img className="rb:w-8 rb:h-8" src={quickOperationIcons[item.key]} />
              <img className="rb:w-4 rb:h-4" src={arrowTopRight} />
            </div>
            <div className="rb:mt-6 rb:text-[#212332] rb:text-[16px] rb:leading-5 rb:font-medium">{t(`dashboard.${item.key}`)}</div>
            <div className="rb:mt-2 rb:text-[#5B6167] rb:text-[12px] rb:font-regular">{t(`dashboard.${item.key}Desc`)}</div>
          </div>
        ))}
      </div>
    </Card>
  )
}
export default QuickOperation