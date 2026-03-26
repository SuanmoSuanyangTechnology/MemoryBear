/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:30:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-19 14:06:38
 */
/**
 * Card Component
 * Collapsible card wrapper for configuration sections
 */

import { type FC, type ReactNode } from 'react'
import clsx from 'clsx';
import { Flex, Space, Tooltip } from 'antd';

import RbCard from '@/components/RbCard/Card'

/**
 * Component props
 */
interface CardProps {
  type?: string;
  title: string | ReactNode;
  subTitle?: string | ReactNode;
  children: ReactNode;
  expanded?: boolean;
  handleExpand?: (type: string) => void;
  className?: string;
  headerClassName?: string;
  bodyClassName?: string;
  extra?: ReactNode;
}

const Card: FC<CardProps> = ({
  type,
  title,
  subTitle,
  children,
  expanded,
  handleExpand,
  className,
  headerClassName,
  bodyClassName,
  extra,
}) => {
  return (
    <RbCard
      title={() => <Flex
        align="center"
        justify="space-between"
        className="rb:font-[MiSans-Bold] rb:font-bold rb:cursor-pointer"
        onClick={type && handleExpand ? () => handleExpand(type) : undefined}
      >
        <Space size={4}>
          {title}
          {subTitle && <Tooltip title={subTitle}>
            <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
          </Tooltip>}
        </Space>
        {handleExpand && <div
          className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')]", {
            'rb:rotate-180': !expanded,
          })}
        ></div>}
      </Flex>}
      headerType="borderless"
      className={className}
      headerClassName={`rb:h-[50px]! rb:pb-[12px]! rb:pt-[16px]! rb:leading-[22px]! rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] ${headerClassName}`}
      bodyClassName={`rb:px-3! rb:py-0! ${expanded ? 'rb:pb-3!' : 'rb:pb-0!'} ${bodyClassName}`}
      extra={extra}
    >
      {(expanded || !(type && handleExpand)) && children}
    </RbCard>
  )
}

export default Card