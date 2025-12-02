import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Skeleton } from 'antd';
import tagEmpty from '@/assets/images/home/tagEmpty.svg'
import Empty from '@/components/Empty';
import Card from './Card';
import { getHotMemoryTags } from '@/api/memory';

const TagList:FC = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState<boolean>(false)
  const [tagList, setTagList] = useState<Array<{ name: string; frequency: number }>>([]);

  useEffect(() => {
    getTagList()
  }, [])

  // 热门记忆标签
  const getTagList = () => {
    setLoading(true)
    getHotMemoryTags().then(res => {
      setTagList(Array.isArray(res) ? res : [])
    }).finally(() => setLoading(false))
  }
  return (
    <Card
      title={t('dashboard.popularMemoryTags')}
    >
      {loading
        ? <Skeleton />
        : !tagList || tagList.length === 0
        ? <Empty url={tagEmpty} title={t('dashboard.activityEmpty')} size={120} className="rb:mt-[36px] rb:mb-[81px]" />
        : <div className="rb:gap-[12px] rb:flex rb:flex-wrap">
          {tagList.map((item, index) => (
            <div
              key={item.name} 
              className={clsx("rb:pt-[6px] rb:pb-[6px] rb:pr-[23px] rb:pl-[20px] rb:border-1 rb:leading-[20px] rb:bg-white rb:rounded-[17px]", {
                'rb:border-[rgba(21,94,239,0.4)] rb:text-[#155EEF]': index % 3 === 0,
                'rb:border-[rgba(255,138,76,0.4)] rb:text-[#FF5D34]': index % 3 === 1,
                'rb:border-[rgba(54,159,33,0.4)] rb:text-[#369F21]': index % 3 === 2, 
              })}
            >{item.name} {item.frequency}</div>
          ))}
        </div>
      }
    </Card>
  )
}
export default TagList