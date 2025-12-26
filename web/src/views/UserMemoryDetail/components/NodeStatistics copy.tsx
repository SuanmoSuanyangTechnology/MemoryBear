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
      title={<>{t('userMemory.nodeStatistics')}<div>{t('userMemory.total')}: {total}</div></>}
      headerType="borderless"
    >
      {loading
        ? <Skeleton />
        : data && data.length > 0
          ? <div className={`rb:w-full rb:grid rb:grid-cols-3 rb:gap-2`}>
            {data.map(vo => (
              <div
                key={vo.type}
                className={clsx("rb:group rb:border rb:border-[#DFE4ED] rb:p-0 rb:rounded-xl rb:hover:shadow-[0px_2px_4px_0px_rgba(0,0,0,0.15)]", {
                  'rb:cursor-pointer': vo.type === 'EMOTIONAL_MEMORY'
                })}
                onClick={() => handleViewDetail(vo.type)}
              >
                <div className="rb:gap-0.5 rb:p-3 rb:leading-4 rb:text-[#5B6167] rb:flex rb:items-center rb:justify-between rb:border-b rb:border-[#DFE4ED]">
                  <div className="rb:wrap-break-word rb:line-clamp-1">{t(`userMemory.${vo.type}`)}</div>
                  {vo.type === 'EMOTIONAL_MEMORY' && <div
                    className="rb:w-3 rb:h-3 rb:-ml-0.75 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/home/arrow_top_right.svg')] rb:group-hover:bg-[url('@/assets/images/home/arrow_top_right_hover.svg')]"
                  ></div>}
                </div>

                <div className="rb:p-3 rb:flex rb:justify-between rb:items-center rb:font-bold rb:text-[20px] rb:text-[#212332] rb:text-left">
                  {vo.count ?? 0}
                  <div className="rb:text-right rb:font-normal rb:text-[14px] rb:text-[#5F6266] rb:leading-4 rb:gap-1">
                    {vo.percentage ?? 0}%
                  </div>
                </div>
              </div>
          ))}
          </div>
        : <Empty size={80} />
      }
    </RbCard>
  )
}
export default NodeStatistics