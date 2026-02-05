/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:21:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-04 13:49:05
 */
/**
 * RbCard Component
 * 
 * A customizable card component that extends Ant Design's Card with:
 * - Multiple header styles (border, borderless, borderBL, borderL)
 * - Avatar support with image or custom component
 * - Flexible padding and styling options
 * - Tooltip support for long titles
 * - Hover effects
 * 
 * @component
 */

import { type FC, type ReactNode } from 'react'
import { Card, Tooltip, Flex } from 'antd';
import clsx from 'clsx';

/** Props interface for RbCard component */
interface RbCardProps {
  /** Additional CSS classes for header */
  headerClassName?: string;
  /** Card title (string, ReactNode, or function) */
  title?: string | ReactNode | (() => ReactNode);
  /** Subtitle text displayed below title */
  subTitle?: string | ReactNode;
  /** Extra content displayed in header (top-right) */
  extra?: ReactNode;
  /** Card body content */
  children?: ReactNode;
  /** Custom avatar component */
  avatar?: ReactNode;
  /** Avatar image URL */
  avatarUrl?: string | null;
  /** Custom padding for card body */
  bodyPadding?: string;
  /** Additional CSS classes for body */
  bodyClassName?: string;
  /** Header style variant */
  headerType?: 'border' | 'borderless' | 'borderBL' | 'borderL';
  /** Background color */
  bgColor?: string;
  /** Card height */
  height?: string;
  /** Additional CSS classes */
  className?: string;
  /** Click handler */
  onClick?: () => void;
  variant?: 'borderL';
}

/** Custom card component with flexible styling and header options */
const RbCard: FC<RbCardProps> = ({
  headerClassName,
  title,
  subTitle,
  extra,
  children,
  avatar,
  avatarUrl,
  bodyPadding,
  bodyClassName: bodyClassNames,
  headerType = 'border',
  bgColor = '#FBFDFF',
  height = 'auto',
  className,
  variant,
  ...props
}) => {
  /** Calculate body padding based on header type and avatar presence */
  const bodyClassName = bodyPadding 
    ? `rb:p-[${bodyPadding}]!`
    : headerType === 'borderL'
    ? 'rb:p-[0_16px_12px_16px]!'
    : avatarUrl || avatar
    ? 'rb:p-[16px_20px_16px_16px]!'
    : (headerType === 'borderless')
    ? 'rb:p-[0_20px_16px_16px]!'
    : (headerType === 'border' && !avatarUrl && !avatar) || headerType === 'borderBL'
    ? 'rb:p-[16px_16px_20px_16px]!'
    : ''
  
  if (variant === 'borderL') {
    return (
      <div
        className="rb:p-[12px_16px] rb:rounded-lg rb:shadow-[inset_4px_0px_0px_0px_#155EEF] rb:border rb:border-[#DFE4ED]"
      >
        <Flex justify="space-between" className={`rb:mb-3! ${headerClassName || ''}`}>
          <Flex vertical gap={4}>
            <div className="rb:font-medium rb:leading-5.5">
              {typeof title === 'function' ? title() : title ?
                <div className="rb:flex rb:items-center">
                  {avatarUrl
                    ? <img src={avatarUrl} className="rb:mr-3.25 rb:w-12 rb:h-12 rb:rounded-lg" />
                    : avatar ? avatar : null
                  }
                  <div className={
                    clsx(
                      {
                        'rb:max-w-full': !avatarUrl && !avatar,
                        'rb:max-w-[calc(100%-60px)]': avatarUrl || avatar,
                      }
                    )
                  }>
                    <div className="rb:w-full rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{title}</div>
                    {subTitle && <div className="rb:text-[#5B6167] rb:text-[12px]">{subTitle}</div>}
                  </div>
                </div> : null
              }
            </div>
            {subTitle && <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{subTitle}</div>}
          </Flex>
          {extra}
        </Flex>
        <div className={bodyClassNames ? bodyClassNames : children ? bodyClassName : 'rb:p-0!'}>
          {children}
        </div>
      </div>
    )
  }
  return (
    <Card
      {...props}
      title={typeof title === 'function' ? title() : title ?
        <div className="rb:flex rb:items-center rb:gap-2">
          {/* Avatar image or custom avatar component */}
          {avatarUrl 
            ? <img src={avatarUrl} className="rb:mr-3.25 rb:w-12 rb:h-12 rb:rounded-lg" />
            : avatar ? avatar : null
          }
          <div className={
            clsx(
              {
                'rb:max-w-full': !avatarUrl && !avatar,
                'rb:max-w-[calc(100%-80px)]': avatarUrl || avatar,
              }
            )
          }>
            {/* Title with tooltip for overflow text */}
            <Tooltip title={title}><div className="rb:w-full rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{title}</div></Tooltip>
            {/* Optional subtitle */}
            {subTitle && <div className="rb:text-[#5B6167] rb:text-[12px]">{subTitle}</div>}
          </div>
        </div> : null
      }
      extra={extra}
      classNames={{
        header: clsx(
          'rb:font-medium',
          {
            /** Borderless header style */
            'rb:border-[0]! rb:text-[16px] rb:p-[0_16px]!': headerType === 'borderless',
            /** Header with avatar */
            'rb:border-[0]! rb:text-[16px] rb:p-[16px_16px_0_16px]!': avatarUrl || avatar,
            /** Standard border header */
            'rb:text-[18px] rb:p-[0]! rb:m-[0_20px]!': headerType === 'border' && !avatarUrl && !avatar,
            /** Border bottom-left style */
            "rb:m-[0_16px]!  rb:p-[0]! rb:relative rb:before:content-[''] rb:before:w-[4px] rb:before:h-[16px] rb:before:bg-[#5B6167] rb:before:absolute rb:before:top-[50%] rb:before:left-[-16px] rb:before:translate-y-[-50%] rb:before:bg-[#5B6167]! rb:before:h-[16px]!": headerType === 'borderBL',
            /** Border left style */
            "rb:m-[0_16px]! rb:p-[0]! rb:leading-[20px] rb:min-h-[48px]! rb:relative rb:border-[0]! rb:before:content-[''] rb:before:w-[4px] rb:before:h-[16px] rb:before:bg-[#5B6167] rb:before:absolute rb:before:top-[50%] rb:before:left-[-16px] rb:before:translate-y-[-50%] rb:before:bg-[#5B6167]! rb:before:h-[16px]!": headerType === 'borderL',
          },
          headerClassName,
        ),
        body: bodyClassNames ? bodyClassNames : children ? bodyClassName : 'rb:p-0!',
      }}
      style={{
        background: bgColor,
        height: height
      }}
      className={`rb:hover:shadow-[0px_2px_4px_0px_rgba(0,0,0,0.15)] ${className}`}
    >
      {children}
    </Card>
  )
}

export default RbCard