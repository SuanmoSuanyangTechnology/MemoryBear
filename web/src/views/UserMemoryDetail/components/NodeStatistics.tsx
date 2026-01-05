import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Skeleton } from 'antd';
import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import {
  getNodeStatistics,
} from '@/api/memory'
import type { NodeStatisticsItem } from '../types'


const BG_LIST = [
  'rb:bg-[linear-gradient(316deg,rgba(21,94,239,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(316deg,rgba(54,159,33,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(314deg,rgba(156,111,255,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(314deg,rgba(255,93,52,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(180deg,rgba(156,111,255,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(180deg,rgba(21,94,239,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(180deg,rgba(54,159,33,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[]',
]

const NodeStatistics: FC = () => {
  const navigate = useNavigate();
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [total, setTotal] = useState<number>(0)
  const [data, setData] = useState<NodeStatisticsItem[]>([])

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  // 记忆洞察
  const getData = () => {
    if (!id) return
    setLoading(true)
    getNodeStatistics(id).then((res) => {
      const response = res as NodeStatisticsItem[]
      setData(response)
      // 计算count总计
      const totalCount = response.reduce((sum, item) => sum + (item.count || 0), 0)
      setTotal(totalCount)
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })
  }
  const handleViewDetail = (type: string) => {
    switch (type) {
      case 'EMOTIONAL_MEMORY':
        navigate(`/statement/${id}`)
        break
    }
  }
  return (
    <RbCard 
      title={<>{t('userMemory.nodeStatistics')} <span className="rb:text-[#5B6167] rb:font-normal!">({t('userMemory.total')}: {total})</span></>}
      headerType="borderless"
    >
      {loading
        ? <Skeleton />
        : data && data.length > 0
          ? <div className={`rb:w-full rb:grid rb:grid-cols-8 rb:gap-3`}>
            {data.map((vo, index) => (
              <div
                key={vo.type}
                className={clsx("rb:flex rb:flex-col rb:justify-between rb:group rb:border rb:border-[#DFE4ED] rb:h-45 rb:rounded-lg rb:pt-3 rb:px-4 rb:pb-5", {
                  'rb:cursor-pointer': vo.type === 'EMOTIONAL_MEMORY'
                }, BG_LIST[index])}
                onClick={() => handleViewDetail(vo.type)}
              >
                <div>
                  <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular">
                    {t(`userMemory.${vo.type}`)}
                  </div>
                  {vo.type === 'EMOTIONAL_MEMORY' && <div
                    className="rb:w-3 rb:h-3 rb:-ml-0.75 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/home/arrow_top_right.svg')] rb:group-hover:bg-[url('@/assets/images/home/arrow_top_right_hover.svg')]"
                  ></div>}
                </div>
                <div className="rb:text-[28px] rb:leading-8.75 rb:font-extrabold">{vo.count ?? 0}</div>
              </div>
            ))}
          </div>
        : <Empty size={80} />
      }
    </RbCard>
  )
}
export default NodeStatistics