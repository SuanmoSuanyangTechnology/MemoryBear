import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Progress } from 'antd';
import RbCard from '@/components/RbCard/Card'
import {
  getImplicitPortrait,
} from '@/api/memory'

interface Item {
  dimension_name: string;
  percentage: number;
  evidence: string[];
  reasoning: string;
  confidence_level: string;
}
interface PortraitItem {
  user_id: string;
  analysis_timestamp: number | string;
  total_summaries_analyzed: number;
  historical_trends: null;
  creativity: Item;
  aesthetic: Item;
  technology: Item;
  literature: Item;
}

const Portrait = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<PortraitItem>({} as PortraitItem)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  const getData = () => {
    if (!id) return
    setLoading(true)
    getImplicitPortrait(id).then((res) => {
      const response = res as PortraitItem
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
      title={t('implicitDetail.portrait')}
      headerType="borderless"
    >
      {loading
        ? <Skeleton active />
        : <div className="rb:mt-1">
          {(['aesthetic', 'creativity', 'literature', 'technology'] as const).map((key) => {
            const item = data[key] as Item
            return (
              <div key={key}>
                <div className="rb:flex rb:justify-between rb:items-center">
                  <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mb-1">{t(`implicitDetail.${key}`)}</div>
                  {item?.percentage ?? 0}%
                </div>
                <Progress percent={item?.percentage || 0} showInfo={false} />
              </div>
            )
          })}
          </div>
        }
    </RbCard>
  )
})
export default Portrait