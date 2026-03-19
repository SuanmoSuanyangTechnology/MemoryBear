/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:30:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-19 14:23:58
 */
/**
 * ResultCard Component
 * Collapsible card wrapper for configuration sections
 */

import { type FC, type ReactNode } from 'react'
import clsx from 'clsx';
import { Flex, Space, Tooltip } from 'antd';

import RbCard from '@/components/RbCard/Card'

/**
 * Component props
 */
interface ResultCardProps {
  title: string | ReactNode;
  subTitle?: string | ReactNode;
  children: ReactNode;
  expanded?: boolean;
  handleExpand?: () => void;
  className?: string;
  headerClassName?: string;
  bodyClassName?: string;
  extra?: ReactNode;
}

const ResultCard: FC<ResultCardProps> = ({
  title,
  subTitle,
  children,
  expanded,
  handleExpand,
  extra,
  className,
  headerClassName,
  bodyClassName,
}) => {
  return (
    <RbCard
      title={() => <Flex
        align="center"
        justify="space-between"
        className="rb:font-[MiSans-Bold] rb:font-bold rb:cursor-pointer"
        onClick={handleExpand}
      >
        <Space size={4}>
          {title}
          {subTitle && <Tooltip title={subTitle}>
            <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
          </Tooltip>}
        </Space>
        <Space size={4}>
          {extra}
          {handleExpand && <div
            className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:transition-transform", {
              'rb:rotate-180': !expanded,
              'rb:rotate-0': expanded,
            })}
          ></div>}
        </Space>
      </Flex>}
      headerType="borderless"
      headerClassName={headerClassName ?? "rb:min-h-[40px]! rb:text-[#212332]! rb:text-[14px]!"}
      bodyClassName={bodyClassName ?? "rb:py-0! rb:px-3!"}
      className={className ?? "rb:bg-[#F6F6F6]!"}
    >
      {(expanded && handleExpand) && children}
    </RbCard>
  )
}

export default ResultCard