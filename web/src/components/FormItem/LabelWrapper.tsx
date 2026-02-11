/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:05:41 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:05:41 
 */
/**
 * LabelWrapper Component
 * 
 * A styled wrapper for displaying form field labels with optional child content.
 * Provides consistent typography and layout for form labels.
 * 
 * @component
 */

import clsx from "clsx";
import type { FC, ReactNode } from "react";

/**
 * @param title - Label text or React node to display
 * @param className - Additional CSS classes for customization
 * @param children - Optional child content to render below the label
 */
const LabelWrapper: FC<{ title: string | ReactNode, className?: string; children?: ReactNode}> = ({title, className, children}) => {
  return (
    <div className={clsx(className)}>
      {/* Label title with consistent styling */}
      <div className="rb:text-[14px] rb:font-medium rb:leading-5">{title}</div>
      {children}
    </div>
  )
}

export default LabelWrapper