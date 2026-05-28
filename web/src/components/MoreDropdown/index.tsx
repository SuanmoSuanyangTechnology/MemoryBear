import { type FC, type MouseEvent } from 'react';
import { Dropdown, Flex } from 'antd';
import type { MenuProps } from 'antd';

interface MoreDropdownProps {
  items: NonNullable<MenuProps['items']>;
  placement?: 'bottomRight' | 'bottomLeft' | 'topRight' | 'topLeft';
  onClick?: (e: MouseEvent) => void;
  iconClassName?: string;
  variant?: 'outline' | 'borderless';
}

/**
 * Dropdown triggered by a "more" icon button.
 * Used in card headers across ApiKeyManagement, Ontology, KnowledgeBase, etc.
 */
const MoreDropdown: FC<MoreDropdownProps> = ({ items, placement = 'bottomRight', onClick, iconClassName = '', variant = 'borderless' }) => {
  return (
    <Dropdown menu={{ items }} placement={placement}>
      {variant === 'outline'
        ? <Flex align="center" justify="center" className="rb:cursor-pointer rb:border rb:border-[#EBEBEB] rb:rounded-lg rb:size-6! rb:hover:border-[#171719]">
          <div
            onClick={(e) => { e.stopPropagation(); onClick?.(e); }}
            className={`rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/more.svg')]`}
          />
        </Flex>
        : 
          <div
            onClick={(e) => { e.stopPropagation(); onClick?.(e); }}
            className={`rb:cursor-pointer rb:size-5.5 rb:bg-cover rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')] ${iconClassName}`}
          />
      }
    </Dropdown>
  );
};

export default MoreDropdown;
