import clsx from "clsx";
import type { FC, ReactNode } from "react";

const DescWrapper: FC<{desc: string | ReactNode, className?: string}> = ({desc, className}) => {
  return (
    <div className={clsx(className, "rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4 ")}>
      {desc}
    </div>
  )
}

export default DescWrapper