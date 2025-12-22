import { type FC, type ReactNode } from 'react'
import { Card } from 'antd';
import clsx from 'clsx';

interface RbCardProps {
  headerClassName?: string;
  title?: string | ReactNode | (() => ReactNode);
  subTitle?: string | ReactNode;
  extra?: ReactNode;
  children?: ReactNode;
  avatar?: ReactNode;
  avatarUrl?: string;
  bodyPadding?: string;
  bodyClassName?: string;
  headerType?: 'border' | 'borderless' | 'borderBL' | 'borderL';
  bgColor?: string;
  height?: string;
  className?: string;
  onClick?: () => void;
}

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
  ...props
}) => {
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
  return (
    <Card
      {...props}
      title={typeof title === 'function' ? title() : title ?
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
      extra={extra}
      classNames={{
        header: clsx(
          'rb:font-medium',
          {
            'rb:border-[0]! rb:text-[16px] rb:p-[0_16px]!': headerType === 'borderless',
            'rb:border-[0]! rb:text-[16px] rb:p-[16px_16px_0_16px]!': avatarUrl || avatar,
            'rb:text-[18px] rb:p-[0]! rb:m-[0_20px]!': headerType === 'border' && !avatarUrl && !avatar,
            "rb:m-[0_16px]!  rb:p-[0]! rb:relative rb:before:content-[''] rb:before:w-[4px] rb:before:h-[16px] rb:before:bg-[#5B6167] rb:before:absolute rb:before:top-[50%] rb:before:left-[-16px] rb:before:translate-y-[-50%] rb:before:bg-[#5B6167]! rb:before:h-[16px]!": headerType === 'borderBL',
            "rb:m-[0_16px]! rb:p-[0]! rb:leading-[20px] rb:min-h-[48px]! rb:relative rb:border-[0]! rb:before:content-[''] rb:before:w-[4px] rb:before:h-[16px] rb:before:bg-[#5B6167] rb:before:absolute rb:before:top-[50%] rb:before:left-[-16px] rb:before:translate-y-[-50%] rb:before:bg-[#5B6167]! rb:before:h-[16px]!": headerType === 'borderL',
          },
          headerClassName,
        ),
        body: bodyClassNames ? bodyClassNames : children ? bodyClassName : 'rb:p-[0]!',
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