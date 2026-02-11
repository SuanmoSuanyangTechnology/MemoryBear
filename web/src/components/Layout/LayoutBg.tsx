/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:13:20 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:13:20 
 */
/**
 * LayoutBg Component
 * 
 * A decorative background component that displays styled background elements.
 * Provides visual aesthetics with positioned decorative shapes.
 * 
 * @component
 */

import { type FC } from 'react';
import clsx from 'clsx';

import styles from './layout.module.css';

/**
 * Background layout component with decorative elements.
 * Renders a fixed full-screen background with styled shapes.
 */
const LayoutBg: FC = () => {
  return (
    <div className="rb:fixed rb:top-0 rb:right-0 rb:left-0 rb:bottom-0 rb:bg-[#FBFDFF]">
      {/* Top section with decorative background shapes */}
      <div className={clsx('rb:h-60', styles.bgTop)}>
        {/* Left decorative element 1 */}
        <div className={clsx(styles.left1)}></div>
        {/* Left decorative element 2 */}
        <div className={clsx(styles.left2)}></div>
        {/* Right decorative element */}
        <div className={clsx(styles.right1)}></div>
      </div>
    </div>
  )
};

export default LayoutBg;