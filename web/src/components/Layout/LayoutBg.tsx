import { type FC } from 'react';
import clsx from 'clsx';
import styles from './layout.module.css';

const LayoutBg: FC = () => {
  return (
    <div className="rb:fixed rb:top-0 rb:right-0 rb:left-0 rb:bottom-0 rb:bg-[#FBFDFF]">
      <div className={clsx('rb:h-[240px]', styles.bgTop)}>
        <div className={clsx(styles.left1)}></div>
        <div className={clsx(styles.left2)}></div>
        <div className={clsx(styles.right1)}></div>
      </div>
    </div>
  )
};

export default LayoutBg;