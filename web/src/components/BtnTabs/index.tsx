/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-19 14:05:09 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-19 14:05:09 
 */
import { type FC } from 'react'
import { Flex } from 'antd';
import clsx from 'clsx'

/** A single tab item with a display label and unique key */
interface Tab {
  label: string
  key: string
}

/** Props for the BtnTabs component */
interface BtnTabsProps {
  /** List of tab items to render */
  items: Tab[]
  /** Key of the currently active tab */
  activeKey: string
  /** Callback fired when a tab is clicked */
  onChange: (key: string) => void;
  /** Optional extra class name for the container */
  className?: string;
  variant?: 'outline' | 'borderless'
}

/** Button-style tab switcher — renders tabs as pill-shaped buttons with active highlight */
const BtnTabs: FC<BtnTabsProps> = ({ items, activeKey, onChange, className, variant = 'borderless' }) => {
  return (
    <Flex align="center" gap={8} className={className || ''}>
      {items.map((tab) => (
        <div
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={clsx('rb:px-2 rb:py-1 rb:rounded-[13px] rb:text-[12px] rb:leading-4.5 rb:cursor-pointer', {
            'rb:bg-[#F6F6F6]': activeKey !== tab.key && variant === 'borderless',
            'rb-border rb:bg-white': activeKey !== tab.key && variant === 'outline',
            'rb:bg-[#171719] rb:text-white rb:border-[#171719]': activeKey === tab.key,
          })}
        >
          {tab.label}
        </div>
      ))}
    </Flex>
  )
}

export default BtnTabs
