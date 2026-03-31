import { type FC } from 'react';

const Header:FC<{ title: string; desc: string; className?: string; }> = ({
  title,
  desc,
  className,
}) => {
  return (
    <div className={`rb:pl-2 ${className}`}>
      <div className="rb:text-[#212332] rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5">{title}</div>
      <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:mt-2">{desc}</div>
    </div>
  )
}

export default Header