import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Space, Skeleton } from 'antd'
import {
  getShortTerm,
} from '@/api/memory'
import Empty from '@/components/Empty'

interface ShortTermItem {
  retrieval: Array<{ query: string; retrieval: string[]; }>;
  message: string;
  answer: string;
}
interface LongTermItem {
  query: string;
  retrieval: string;
}
interface ShortData {
  short_term: ShortTermItem[];
  long_term: LongTermItem[];
  entity: number;
  retrieval_number: number;
  long_term_number: number;
}
const ShortTermDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<ShortData>({} as ShortData)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  const getData = () => {
    if (!id) return
    setLoading(true)
    getShortTerm(id).then((res) => {
      const response = res as ShortData
      setData(response)
      setLoading(false)
    })
    .finally(() => {
      setLoading(false)
    })
  }

  return (
    <div className="rb:h-full rb:max-w-266 rb:mx-auto">
      <div className="rb:flex rb:justify-between rb:items-center rb:text-[#FFFFFF] rb:leading-5 rb:h-30 rb:p-5 rb:bg-[url('@/assets/images/userMemory/shortTerm.png')] rb:bg-cover">
        <div className="rb:max-w-135">{t('shortTermDetail.title')}</div>

        <div className="rb:grid rb:grid-cols-3 rb:gap-4">
          {(['retrieval_number', 'entity', 'long_term_number'] as const).map(key => (
            <div key={key} className="rb:bg-[rgba(255,255,255,0.2)] rb:rounded-lg rb:p-3.5 rb:text-[12px] rb:text-center">
              <div className="rb:text-[24px] rb:leading-8 rb:mb-1">{(data as any)[key] ?? 0}</div>
              {t(`shortTermDetail.${key}`)}
            </div>
          ))}
        </div>
      </div>


      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('shortTermDetail.shortTermTitle')}</div>
      <div className="rb:my-3 rb:text-[#5B6167] rb:leading-5">{t('shortTermDetail.shortTermSubTitle')}</div>
      <Space size={16} direction="vertical" className="rb:w-full">
        {loading
          ? <Skeleton active />
          : !data.short_term || data.short_term.length === 0
          ? <Empty />
          :data.short_term?.map((vo, voIdx) => (
            <div key={voIdx} className="rb:leading-5 rb:shadow-[inset_3px_0px_0px_0px_#155EEF] rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:px-6 rb:py-3">
              <div className="rb:font-medium rb:text-[16px] rb:leading-5.5 rb:mb-3">{vo.message}</div>
              <Space size={16} direction="vertical" className="rb:w-full">
                {vo.retrieval.map((item, index) => (
                  <div key={index} className="rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-3 rb:py-2.5 rb:leading-5">
                    <div className="rb:font-medium rb:mb-3">{t('shortTermDetail.query')}: {item.query}</div>
                    <div className="rb:font-medium rb:leading-5 rb:mb-1">{t('shortTermDetail.answer')}:</div>
                    {item.retrieval.length > 0 ? item.retrieval.map((retrieval, retrievalIdx) => (
                      <div key={retrievalIdx} className="rb:text-[#5B6167] rb:text-[12px]">- {retrieval}</div>
                    )) : <div className="rb:text-[#5B6167] rb:text-[12px]">{t('shortTermDetail.noAnswer')}</div>}
                  </div>
                ))}
                <div>
                  <div className="rb:font-medium rb:leading-5 rb:mb-1">{t('shortTermDetail.answer')}</div>
                  <div className="rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-3 rb:py-2.5 rb:leading-5">{vo.answer}</div>
                </div>
              </Space>
            </div>
          ))
        }
      </Space>

      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('shortTermDetail.longTermTitle')}</div>
      <div className="rb:my-3 rb:text-[#5B6167] rb:leading-5">{t('shortTermDetail.shortTermSubTitle')}</div>
      <Space size={16} direction="vertical" className="rb:w-full">
        {loading
          ? <Skeleton active />
          : !data.long_term || data.long_term.length === 0
          ? <Empty />
          : data.long_term?.map((vo, voIdx) => (
            <div key={voIdx} className="rb:leading-5 rb:shadow-[inset_3px_0px_0px_0px_#155EEF] rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:px-6 rb:py-3">
              <div className="rb:mb-1 rb:font-medium rb:leading-5.5">{vo.query}</div>
              <div className="rb:mt-1 rb:leading-5 rb:text-[#5B6167] rb:text-[12px]">{vo.retrieval}</div>
            </div>
          ))
        }
      </Space>
    </div>
  )
}
export default ShortTermDetail