import { type FC } from 'react'
import { Descriptions, type DescriptionsProps } from 'antd'

import styles from './index.module.css'

const RbDescriptions: FC<DescriptionsProps> = ({
  items,
}) => {
  return (
    <Descriptions bordered column={1} className={styles.rbDescriptions} items={items} />
  )
}

export default RbDescriptions