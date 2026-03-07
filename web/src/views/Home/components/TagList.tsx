/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:15:04 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-26 11:15:15
 */
/**
 * Tag List Component
 * Displays popular memory tags with frequency counts
 */

import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Skeleton, Flex } from 'antd';

import tagEmpty from '@/assets/images/home/tagEmpty.svg'
import Empty from '@/components/Empty';
import Card from './Card';
import { getHotMemoryTags } from '@/api/memory';

const btnStyleList = [
  'rb:bg-[rgba(21,94,239,0.06)]',
  'rb:bg-[rgba(33,35,50,0.06)]',
  'rb:bg-[rgba(156,111,255,0.06)]'
]
const numStyleList = [
  'rb:bg-[#155EEF]',
  'rb:bg-[#212332]',
  'rb:bg-[#9C6FFF]'
]

const TagList:FC = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState<boolean>(false)
  const [tagList, setTagList] = useState<Array<{ name: string; frequency: number }>>([]);

  useEffect(() => {
    getTagList()
  }, [])

  /** Fetch popular memory tags */
  const getTagList = () => {
    setLoading(true)
    getHotMemoryTags().then(res => {
      setTagList(Array.isArray(res) ? res : [])
    }).finally(() => setLoading(false))
  }
  return (
    <Card
      title={t('dashboard.popularMemoryTags')}
      bodyClassName='rb:overflow-hidden! rb:pt-0! rb:pb-4! rb:pl-4! rb:pr-3.25!'
      className="rb:min-h-[calc(100vh-744px)]"
    >
      {loading
        ? <Skeleton />
        : !tagList || tagList.length === 0
        ? <Empty url={tagEmpty} title={t('dashboard.activityEmpty')} size={120} className="rb:mt-9 rb:mb-20.25" />
        : <Flex wrap className="rb:gap-x-3! rb:gap-y-2.5!">
          {tagList.map((item, index) => (
            <div
              key={item.name} 
              className={clsx("rb:rounded-[17px] rb:py-1.5 rb:pl-3 rb:pr-2", btnStyleList[index % 3])}
            >
              {item.name}
              <span className={clsx('rb:px-2 rb:py-0.5 rb:rounded-[10px] rb:text-[#FFFFFF] rb:text-[12px] rb:font-bold rb:font-[MiSans-Demibold] rb:ml-2', numStyleList[index % 3])}>{item.frequency}</span>
            </div>
          ))}
          </Flex>
      }
    </Card>
  )
}
export default TagList