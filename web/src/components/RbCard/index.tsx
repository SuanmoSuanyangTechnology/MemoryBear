/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:21:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 20:24:43
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
import { Card, Tooltip, Flex, type CardProps } from 'antd';
import clsx from 'clsx';

/** Props interface for RbCard component */
interface RbCardProps extends CardProps {
  isNeedTooltip?: boolean;
  children?: ReactNode;
  /** Custom avatar component */
  avatarText?: string;
  avatarClassName?: string;
  /** Avatar image URL */
  avatarUrl?: string | null;
  /** Click handler */
  onClick?: () => void;
  footer?: ReactNode;
  headerClassName?: string;
  titleClassName?: string;
}

/** Custom card component with flexible styling and header options */
const RbCard: FC<RbCardProps> = ({
  isNeedTooltip = true,
  title,
  children,
  avatarText,
  avatarClassName,
  avatarUrl,
  footer,
  headerClassName,
  titleClassName,
  className,
  ...props
}) => {
  return (
    <Card
      variant="borderless"
      {...props}
      title={<Flex align="center" gap={12}>
        {avatarUrl
          ? <img src={avatarUrl} alt={avatarUrl} className="rb:size-12 rb:rounded-lg" />
          : avatarText
            ? <Flex align="center" justify="center" className={clsx(avatarClassName, "rb:size-11 rb:rounded-lg rb:text-[24px] rb:text-[#ffffff] rb:bg-[#155EEF]")}>{avatarText}</Flex> : null
        }
        {isNeedTooltip
          ? <Tooltip title={title}>
            <div className={`rb:flex-1 rb:leading-5.5 rb:min-w-0 rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-2 ${titleClassName}`}>
              {title}
            </div>
          </Tooltip>
          : <div className="rb:flex-1 rb:leading-5.5 rb:min-w-0 rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-2">
            {title}
          </div>
        }
        
      </Flex>}
      classNames={{
        header: `rb:text-[16px] rb:p-[16px_16px_8px_16px]! rb:border-0! ${headerClassName}`,
        body: 'rb:p-4! rb:pt-2! rb:bg-white!',
      }}
      className={`rb:hover:shadow-[0px_2px_8px_0px_rgba(23,23,25,0.16)]! rb:group ${className}`}
    >
      {children}

      {footer ? <div className="rb:mt-6">{footer}</div> : null}
    </Card>
  )
}

export default RbCard