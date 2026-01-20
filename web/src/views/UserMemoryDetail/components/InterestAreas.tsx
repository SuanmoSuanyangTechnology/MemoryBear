import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Progress } from 'antd';
import RbCard from '@/components/RbCard/Card'
import {
  getImplicitInterestAreas,
} from '@/api/memory'

interface Item {
  category_name: string;
  percentage: number;
  evidence: string[];
  trending_direction: string | null;
}
interface InterestAreasItem {
  user_id: string;
  analysis_timestamp: number | string;
  total_summaries_analyzed: number;
  tech: Item;
  lifestyle: Item;
  music: Item;
  art: Item;
}

const InterestAreas = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<InterestAreasItem>({} as InterestAreasItem)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  const getData = () => {
    if (!id) return
    setLoading(true)
    getImplicitInterestAreas(id).then((res) => {
      const response = res as InterestAreasItem
      setData(response)
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })
  }

  useImperativeHandle(ref, () => ({
    handleRefresh: getData
  }));
  return (
    <RbCard
      title={t('implicitDetail.interestAreas')}
      headerType="borderless"
    >
      {loading
        ? <Skeleton active />
        : <div>
          {(['art', 'music', 'tech', 'lifestyle'] as const).map((key) => {
            return (
              <div key={key} >
                <div className="rb:flex rb:justify-between rb:items-center">
                  <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mb-1">{t(`implicitDetail.${key}`)}</div>
                  {data[key]?.percentage ?? 0}%
                </div>
                <Progress percent={data[key]?.percentage || 0} showInfo={false} />
              </div>
            )
          })}
          </div>
        }
    </RbCard>
  )
})
export default InterestAreas