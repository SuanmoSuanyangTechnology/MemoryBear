import clsx from "clsx";
import type { FC, ReactNode } from "react";

const LabelWrapper: FC<{ title: string | ReactNode, className?: string; children?: ReactNode}> = ({title, className, children}) => {
  return (
    <div className={clsx(className)}>
      <div className="rb:text-[14px] rb:font-medium rb:leading-5">{title}</div>
      {children}
    </div>
  )
}

export default LabelWrapper