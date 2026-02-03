/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:05:16 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:05:16 
 */
/**
 * DescWrapper Component
 * 
 * A styled wrapper for displaying description text in forms.
 * Provides consistent typography and styling for form field descriptions.
 * 
 * @component
 */

import clsx from "clsx";
import type { FC, ReactNode } from "react";

/**
 * @param desc - Description content (string or React node)
 * @param className - Additional CSS classes for customization
 */
const DescWrapper: FC<{desc: string | ReactNode, className?: string}> = ({desc, className}) => {
  return (
    <div className={clsx(className, "rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ")}>
      {desc}
    </div>
  )
}

export default DescWrapper