/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:16:38 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-11 14:57:35
 */
/**
 * Quick Operation Component
 * Displays shortcut cards for common operations
 * Includes navigation to application, knowledge base, memory conversation, and help center
 */

import { type FC } from 'react'
import clsx from 'clsx';
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom';
import { Flex } from 'antd';

import Card from './Card';
import applicationIcon from '@/assets/images/home/application.svg';
import knowledgeIcon from '@/assets/images/home/knowledge.svg';
import memoryConversationIcon from '@/assets/images/home/memoryConversation.svg';
import helpCenterIcon from '@/assets/images/menu/helpCenter_active.svg'

/** Quick operation items configuration */
const quickOperations = [
  { key: 'createNewApplication', url: '/application' },
  { key: 'createNewKnowledge', url: '/knowledge-base' },
  { key: 'memoryConversation', url: '/memory-conversation' },
  { key: 'helpCenter', url: '' },
]

const bgStyleList = [
  'rb:bg-[rgba(21,94,239,0.1)]',
  'rb:bg-[rgba(156,111,255,0.1)]',
  'rb:bg-[rgba(255,176,72,0.1)]',
  'rb:bg-[rgba(77,168,255,0.1)]'
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
      bodyClassName="rb:pt-0! rb:pb-[14px]! rb:px-4!"
    >
      <div className="rb:grid rb:grid-cols-1 rb:gap-3">
        {quickOperations.map((item, index) => (
          <Flex key={item.key} align="center" gap={20} className={clsx("rb:relative rb:rounded-xl rb:py-2! rb:px-3! rb:cursor-pointer", bgStyleList[index])} onClick={() => handleJump(item.url)}>
            <div className="rb:size-8 rb:rounded-lg rb:p-1 rb:bg-[#FFFFFF]">
              <img className="rb:size-6" src={quickOperationIcons[item.key]} />
            </div>
            <div>
              <div className="rb:text-[14px] rb:leading-5 rb:font-medium">{t(`dashboard.${item.key}`)}</div>
              <div className="rb:mt-0.5 rb:text-[#5B6167] rb:text-[12px] rb:font-regular">{t(`dashboard.${item.key}Desc`)}</div>
            </div>
          </Flex>
        ))}
      </div>
    </Card>
  )
}
export default QuickOperation