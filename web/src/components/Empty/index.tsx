/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:03:25 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-13 11:48:51
 */
/**
 * Empty Component
 * 
 * A customizable empty state component that displays an icon with optional title and subtitle.
 * Used to indicate when no data or content is available.
 * 
 * @component
 */

import { type FC, type ReactElement } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex } from 'antd';

import emptyIcon from '@/assets/images/empty/empty.svg';

interface EmptyProps {
  /** Custom icon URL for the empty state */
  url?: string;
  /** Icon size - single number or [width, height] array */
  size?: number | number[];
  /** Main title text */
  title?: string | ReactElement;
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
    <div
      className={className}
    >
      {/* Outer column wrapper: min-h-full = guarantees at least the parent's height
          so vertical centering below kicks in when there's room. */}
      <Flex
        vertical
        className="rb:min-h-full!"
      >
        {/* Content wrapped with margin-y:auto — SAFE centering pattern.
            - parent height >= content height: my-auto centers vertically
            - parent height <  content height: margin-top:auto collapses to 0,
              content starts at the very top and parent's overflow-y:auto can
              scroll everything (icon top never gets clipped). */}
        <div className="rb:my-auto! rb:w-full! rb:py-4!" style={{ marginTop: 'auto', marginBottom: 'auto' }}>
          <Flex align="center" justify="center" vertical>
            {/* Empty state icon */}
            <img src={url || emptyIcon} alt="404" style={{ width: `${width}px`, height: `${height}px` }} />
            {/* Optional title */}
            {title && <div className="rb:mt-2 rb:leading-5 rb:text-[#212332]">{title}</div>}
            {/* Optional subtitle with conditional styling */}
            {curSubTitle && <div className={`rb:mt-[${url ? 8 : 5}px] rb:leading-4 rb:text-[12px] rb:text-[#5B6167]`}>{curSubTitle}</div>}
          </Flex>
        </div>
      </Flex>
    </div>
  );
}
export default Empty;