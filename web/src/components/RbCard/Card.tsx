/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:21:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 16:20:39
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
  titleClassName?: string;
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
  variant?: 'borderL' | 'borderless' | 'outlined';
}

/** Custom card component with flexible styling and header options */
const RbCard: FC<RbCardProps> = ({
  headerClassName,
  title,
  titleClassName,
  subTitle,
  extra,
  children,
  avatar,
  avatarUrl,
  bodyPadding,
  bodyClassName: bodyClassNames,
  headerType = 'border',
  bgColor = '#FFFFFF',
  height = 'auto',
  className,
  variant = 'borderless',
  ...props
}) => {
  /** Calculate body padding based on header type and avatar presence */
  const bodyClassName = bodyPadding 
    ? `rb:p-[${bodyPadding}]!`
    : headerType === 'borderL'
    ? 'rb:p-[0_16px_12px_16px]!'
    : avatarUrl || avatar
    ? 'rb:p-4!'
    : (headerType === 'borderless')
    ? 'rb:p-[0_20px_16px_16px]!'
    : (headerType === 'border' && !avatarUrl && !avatar) || headerType === 'borderBL'
    ? 'rb:p-[16px_16px_20px_16px]!'
    : ''
  
  if (variant === 'borderL') {
    return (
      <div
        className="rb:p-[12px_16px] rb:rounded-lg rb:shadow-[inset_4px_0px_0px_0px_#155EEF] rb-border"
      >
        <Flex justify="space-between" className={`rb:mb-3! ${headerClassName || ''}`}>
          <Flex vertical gap={4}>
            <div className="rb:font-medium rb:leading-5.5">
              {typeof title === 'function' ? title() : title ?
                <Flex align="center">
                  {avatarUrl
                    ? <img src={avatarUrl} alt={avatarUrl} className="rb:mr-3.25 rb:size-12 rb:rounded-lg" />
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
                    <div className={`rb:w-full rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap ${titleClassName}`}>{title}</div>
                    {subTitle && <div className="rb:text-[#5B6167] rb:text-[12px]">{subTitle}</div>}
                  </div>
                </Flex> : null
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
      variant={variant}
      {...props}
      title={typeof title === 'function' ? title() : title ?
        <Flex align="center" gap={12}>
          {/* Avatar image or custom avatar component */}
          {avatarUrl 
            ? <img src={avatarUrl} alt={avatarUrl} className="rb:mr-3.25 rb:size-12 rb:rounded-lg" />
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
            <Tooltip title={title}><div className={`rb:w-full rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap ${titleClassName}`}>{title}</div></Tooltip>
            {/* Optional subtitle */}
            {subTitle && <div className="rb:text-[#5B6167] rb:text-[12px]">{subTitle}</div>}
          </div>
        </Flex> : null
      }
      extra={extra}
      classNames={{
        header: clsx(
          'rb:font-medium',
          {
            /** Borderless header style */
            'rb:border-[0]! rb:text-[16px] rb:p-[0_16px]! rb:min-h-10!': headerType === 'borderless',
            /** Header with avatar */
            'rb:border-[0]! rb:text-[16px] rb:p-[16px_16px_0_16px]!': avatarUrl || avatar,
            /** Standard border header */
            'rb:text-[18px] rb:p-[0]! rb:m-[0_20px]! rb:border-b-[0.5px]!': headerType === 'border' && !avatarUrl && !avatar,
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
        height: height,
        border: variant === 'outlined' ? '1px solid #EBEBEB' :  'none'
      }}
      className={clsx({
        'rb:shadow-none!': variant === 'borderless' || variant === 'outlined',
        'rb:hover:shadow-[0px_2px_8px_0px_rgba(23,23,25,0.16)]!': variant !== 'borderless' && variant !== 'outlined'
      }, className)}
    >
      {children}
    </Card>
  )
}

export default RbCard