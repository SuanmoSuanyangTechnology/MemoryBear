import { memo } from 'react'
import type { FC, ReactNode } from 'react'
import { Button } from 'antd'

interface RbButtonProps {
  node: {
    children: ReactNode;
  };
  children: string[]
}
const RbButton: FC<RbButtonProps> = (props) => {
  console.log('RbButton', props)
  const { children } = props;

  return (
    <Button>
      {children}
    </Button>
  )
}
export default memo(RbButton)
