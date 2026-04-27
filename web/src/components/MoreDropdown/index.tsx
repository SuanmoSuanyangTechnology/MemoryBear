import { type FC, type MouseEvent } from 'react';
import { Dropdown } from 'antd';
import type { MenuProps } from 'antd';

interface MoreDropdownProps {
  items: NonNullable<MenuProps['items']>;
  placement?: 'bottomRight' | 'bottomLeft' | 'topRight' | 'topLeft';
  onClick?: (e: MouseEvent) => void;
}

/**
 * Dropdown triggered by a "more" icon button.
 * Used in card headers across ApiKeyManagement, Ontology, KnowledgeBase, etc.
 */
const MoreDropdown: FC<MoreDropdownProps> = ({ items, placement = 'bottomRight', onClick }) => {
  return (
    <Dropdown menu={{ items }} placement={placement}>
      <div
        onClick={(e) => { e.stopPropagation(); onClick?.(e); }}
        className="rb:cursor-pointer rb:size-5.5 rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"
      />
    </Dropdown>
  );
};

export default MoreDropdown;
