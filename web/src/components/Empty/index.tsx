/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:03:25 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:47:31
 */
/**
 * Empty Component
 * 
 * A customizable empty state component that displays an icon with optional title and subtitle.
 * Used to indicate when no data or content is available.
 * 
 * @component
 */

import { type FC } from 'react';
import { useTranslation } from 'react-i18next';

import emptyIcon from '@/assets/images/empty/empty.svg';

interface EmptyProps {
  /** Custom icon URL for the empty state */
  url?: string;
  /** Icon size - single number or [width, height] array */
  size?: number | number[];
  /** Main title text */
  title?: string;
  /** Whether to show subtitle */
  isNeedSubTitle?: boolean;
  /** Custom subtitle text */
  subTitle?: string;
  /** Additional CSS classes */
  className?: string;
}
const  Empty: FC<EmptyProps> = ({
  url,
  size = 200,
  title,
  isNeedSubTitle = true,
  subTitle,
  className = '',
}) => {
  const { t } = useTranslation();
  // Calculate width and height from size prop (supports single value or [width, height] array)
  const width = Array.isArray(size) ? size[0] : size ? size : url ? 200 : 88;
  const height = Array.isArray(size) ? size[1] : size ? size : url ? 200 : 88;
  
  // Use custom subtitle or default translation if subtitle is needed
  const curSubTitle = isNeedSubTitle ? (subTitle || t('empty.tableEmpty')) : null;
  return (
    <div className={`rb:flex rb:items-center rb:justify-center rb:flex-col ${className}`}>
      {/* Empty state icon */}
      <img src={url || emptyIcon} alt="404" style={{ width: `${width}px`, height: `${height}px` }} />
      {/* Optional title */}
      {title && <div className="rb:mt-2 rb:leading-5">{title}</div>}
      {/* Optional subtitle with conditional styling */}
      {curSubTitle && <div className={`rb:mt-[${url ? 8 : 5}px] rb:leading-4 rb:text-[12px] rb:text-[#A8A9AA]`}>{curSubTitle}</div>}
    </div>
  );
}
export default Empty;