import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Progress } from 'antd'
import type { Data } from '../types'

import Tag from '@/components/Tag'

const bgList = [
  'linear-gradient( 180deg, #F1F6FE 0%, #FBFDFF 100%)',
  'linear-gradient( 180deg, #F1F9FE 0%, #FBFDFF 100%)',
  'linear-gradient( 180deg, #FEFBF7 0%, #FBFDFF 100%)',
  'linear-gradient( 180deg, #F1F9FE 0%, #FBFDFF 100%)',
]
interface CardListProps {
  data: Data[]
  handleViewDetail: (id: string | number) => void
}
const CardList: FC<CardListProps> = ({
  data,
  handleViewDetail
}) => {
  const { t } = useTranslation();

  return (
    <div className="rb:grid rb:grid-cols-3 rb:gap-[16px]">
      {data.map((item, index) => {
        return (
          <div
            key={item.id}
            className="rb:p-[20px] rb:rounded-[12px] rb:border-[1px] rb:border-[#DFE4ED] rb:cursor-pointer"
            style={{
              background: bgList[index % bgList.length],
            }}
            onClick={() => handleViewDetail(item.id)}
          >
            <div className="rb:flex rb:items-center">
              <div className="rb:w-[48px] rb:h-[48px] rb:text-center rb:font-semibold rb:text-[28px] rb:leading-[48px] rb:rounded-[8px] rb:text-[#FBFDFF] rb:bg-[#155EEF]">{item.username[0]}</div>
              <div className="rb:text-base rb:font-medium rb:leading-[24px] rb:ml-[12px]">
                {item.username}<br/>
                <Tag color={item.role === 'administrator' ? 'processing' : 'error'}>{item.role}</Tag>
              </div>
            </div>
            <div className="rb:grid rb:grid-cols-3 rb:gap-[12px] rb:mt-[28px] rb:mb-[28px]">
              {['knowledgeEntryCount', 'interactionCount', 'averageTimeConsumption'].map(key => (
                <div key={key} className="rb:text-center">
                  <div className="rb:text-[24px] rb:leading-[30px] rb:font-extrabold">{item[key] || 0}</div>
                  <div className="rb:break-words">{t(`memory.${key}`)}</div>
                </div>
              ))}
            </div>
            
            <div className="rb:flex rb:items-center rb:justify-between rb:w-full rb:text-[#5B6167] rb:text-[12px]">{t('memory.dataCompletionDegree')}<span>{item.dataCompletionDegree || 0}%</span></div>
            <Progress percent={item.dataCompletionDegree || 0} showInfo={false} size={{height: 8}} />
          </div>
        )
      })}
    </div>
  )
}

export default CardList