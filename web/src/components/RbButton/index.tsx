import { type FC } from 'react'
import clsx from 'clsx'
import { Button, type ButtonProps } from 'antd'

import styles from './index.module.css'

const RbButton: FC<ButtonProps> = ({
  children,
  className,
  ...props
}) => {
  return (
    <Button
      className={clsx(styles.rbButton, className, "rb:hover:shadow-[0px_2px_8px_0px_rgba(23,23,25,0.16)]")}
      {...props}
    >
      {children}
    </Button>
  )
}

export default RbButton