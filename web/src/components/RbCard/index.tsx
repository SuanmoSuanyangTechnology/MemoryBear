import { type FC, type ReactNode } from 'react'
import { Card } from 'antd';
import clsx from 'clsx';

interface RbCardProps {
  title?: string | ReactNode;
  subTitle?: string;
  extra?: ReactNode;
  children: ReactNode;
  avatar?: ReactNode;
  className?: string;
}

const RbCard: FC<RbCardProps> = ({
  title,
  subTitle,
  extra,
  children,
  avatar,
  className,
}) => {
  if (avatar) {
    return (
      <Card
        classNames={{
          header: 'rb:p-[0]! rb:m-[0_20px]!',
          body: 'rb:p-[16px_20px_16px_16px]',
        }}
        style={{
          background: '#FBFDFF'
        }}
      >
        {title &&
          <div className={clsx("rb:text-[#212332] rb:text-[16px] rb:font-medium rb:flex rb:items-center rb:mb-[20px]", {
            'rb:justify-between': extra
          })}>
            <div className="rb:flex rb:items-center">
              <div className="rb:mr-[13px] rb:w-[48px] rb:h-[48px] rb:rounded-[8px] rb:overflow-hidden">{avatar}</div>
              <div className="rb:truncate">{title}</div>
            </div>
            {subTitle && <div className="rb:text-[#5B6167] rb:text-[12px]">{subTitle}</div>}
            {extra}
          </div>
        }
        {children}
      </Card>
    )
  }
  return (
    <Card
      title={ title ?
        <div className={clsx("rb:text-[#212332] rb:text-[18px] rb:font-medium rb:flex rb:items-center", {
          'rb:justify-between': extra
        })}>
          <div className="rb:truncate">{title}</div>
          {subTitle && <div className="rb:text-[#5B6167] rb:text-[12px]">{subTitle}</div>}
          {extra}
        </div> : null
      }
      classNames={{
        header: 'rb:p-[0]! rb:m-[0_20px]!',
        body: `rb:p-[16px_20px_20px_16px] ${className || ''}`,
      }}
      style={{
        background: '#FBFDFF'
      }}
    >
      {children}
    </Card>
  )
}

export default RbCard