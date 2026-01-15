/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-05 17:22:23
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-15 14:55:51
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

const quickOperations = [
  { key: 'createNewApplication', url: '/application' },
  { key: 'createNewKnowledge', url: '/knowledge-base' },
  { key: 'memoryConversation', url: '/memory-conversation' },
  { key: 'helpCenter', url: '' },
]

const quickOperationIcons: {[key: string]: string | undefined} = {
  createNewApplication: applicationIcon,
  createNewKnowledge: knowledgeIcon,
  memoryConversation: memoryConversationIcon,
  helpCenter: helpCenterIcon
}
const QuickOperation:FC = () => {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate();

  const handleJump = (url: string | null) => {
    if (url) {
      navigate(url)
    }else{
      const currentLang = i18n.language;
      const lang = currentLang === 'zh' ? 'zh' : 'en';
      const helpUrl = `https://docs.redbearai.com/s/${lang}-memorybear`;
      
      // 创建隐藏的 a 标签来避免弹窗拦截
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
      <div className="rb:grid rb:grid-cols-4 rb:gap-[16px]">
        {quickOperations.map(item => (
          <div key={item.key} className="rb:rounded-[8px] rb:p-[20px_16px] rb:border-1 rb:border-[#DFE4ED] rb:cursor-pointer rb:hover:border-[#155EEF]" onClick={() => handleJump(item.url)}>
            <div className="rb:flex rb:justify-between">
              <img className="rb:w-[32px] rb:h-[32px]" src={quickOperationIcons[item.key]} />
              <img className="rb:w-[16px] rb:h-[16px]" src={arrowTopRight} />
            </div>
            <div className="rb:mt-[24px] rb:text-[#212332] rb:text-[16px] rb:leading-[20px] rb:font-medium">{t(`dashboard.${item.key}`)}</div>
            <div className="rb:mt-[8px] rb:text-[#5B6167] rb:text-[12px] rb:font-regular">{t(`dashboard.${item.key}Desc`)}</div>
          </div>
        ))}
      </div>
    </Card>
  )
}
export default QuickOperation