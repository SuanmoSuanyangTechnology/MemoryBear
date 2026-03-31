/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-10 11:08:27 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 15:38:31
 */
/*
 * PageHeader Component
 * 
 * A reusable page header component that provides a consistent layout for page titles,
 * subtitles, and action buttons across the application.
 * 
 * Features:
 * - Displays a main title and optional subtitle
 * - Supports custom action buttons or extra content on the right side
 * - Fixed height (64px) with responsive layout
 * - Text overflow handling with ellipsis
 * - Consistent spacing and styling using Tailwind CSS
 */
import { type FC, type ReactNode } from 'react';
import { Layout, Flex } from 'antd';
import clsx from 'clsx';

const { Header } = Layout;
interface ConfigHeaderProps {
  avatarUrl?: string | null;
  avatarText?: string;
  avatarClassName?: string;
  title?: ReactNode | string;
  operation?: ReactNode;
  extra?: ReactNode;
  centerContent?: ReactNode;
}

const PageHeader: FC<ConfigHeaderProps> = ({
  avatarUrl,
  avatarText,
  avatarClassName,
  title,
  operation,
  extra,
  centerContent
}) => {
  return (
    // Main header container: full width, 64px height, flex layout with space between
    <Header className={`rb:w-full rb:h-16! rb:grid rb:grid-cols-${extra && centerContent ? '3' : ((extra && !centerContent) || (!extra && centerContent)) ? '2': 1} rb:gap-6 rb:px-4! rb:bg-white!`}>
      <Flex align="center" gap={8}>
        {avatarUrl
          ? <img src={avatarUrl} alt={avatarUrl} className="rb:size-8 rb:rounded-lg rb:mr-2" />
          : avatarText
            ? <Flex align="center" justify="center" className={clsx(avatarClassName, "rb:size-8 rb:rounded-lg rb:text-[24px] rb:text-[#ffffff] rb:bg-[#155EEF] rb:mr-2")}>{avatarText}</Flex> : null
        }
        {/* Left section: Title and subtitle */}
        <div>
          {/* Main title: 18px font, semibold, [#212332] color, single line with ellipsis */}
          <div className="rb:leading-5 rb:text-[14px] rb:text-[#212332] rb:font-medium rb:wrap-break-word rb:line-clamp-1">{title}</div>
        </div>
        {operation}
      </Flex>

      {centerContent && <Flex align="center" justify="center">
        {centerContent}
      </Flex>}
      {/* Right section: Extra content (buttons, filters, etc.) */}
      <Flex align="center" justify="end" gap={12}>
        {extra}
      </Flex>
    </Header>
  );
};

export default PageHeader;