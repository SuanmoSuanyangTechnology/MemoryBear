import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom';
import Card from './Card';
import applicationIcon from '@/assets/images/menu/application_active.svg';
import knowledgeIcon from '@/assets/images/menu/knowledge_active.svg';
import memoryConversationIcon from '@/assets/images/menu/memoryConversation_active.svg';
import arrowTopRight from '@/assets/images/home/arrow_top_right.svg';

const quickOperations = [
  { key: 'createNewApplication', url: '/application' },
  { key: 'createNewKnowledge', url: '/knowledge-base' },
  { key: 'memoryConversation', url: '/memory-conversation' },
]

const quickOperationIcons: {[key: string]: string | undefined} = {
  createNewApplication: applicationIcon,
  createNewKnowledge: knowledgeIcon,
  memoryConversation: memoryConversationIcon,
}
const QuickOperation:FC = () => {
  const { t } = useTranslation()
  const navigate = useNavigate();

  const handleJump = (url: string | null) => {
    if (url) {
      navigate(url)
    }
  }
  return (
    <Card
      title={t('dashboard.quickOperation')}
    >
      <div className="rb:grid rb:grid-cols-3 rb:gap-[16px]">
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