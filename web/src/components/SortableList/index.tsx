/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:27:36 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:27:36 
 */
/**
 * SortableList Component
 * 
 * A drag-and-drop sortable list with:
 * - Vertical drag-and-drop reordering
 * - Editable text inputs for each item
 * - Add new item functionality
 * - Integration with @dnd-kit library
 * 
 * @component
 */

import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import type { DragEndEvent } from '@dnd-kit/core';
import { DndContext } from '@dnd-kit/core';
import { restrictToVerticalAxis } from '@dnd-kit/modifiers';
import {
  arrayMove,
  SortableContext,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { List, Input, Button } from 'antd';

import SortableListItem from './SortableListItem';
import DragHandle from './DragHandle';

/** Item interface for sortable list */
interface Item {
  /** Unique key for the item */
  key: number;
  /** Text content of the item */
  content: string;
  /** Special type for add button */
  type?: 'add';
}

/** Props interface for SortableList component */
interface SortableListProps {
  /** Array of list items */
  value?: Item[];
  /** Callback fired when items change */
  onChange?: (items?: Item[]) => void;
}

/** Sortable list component with drag-and-drop functionality */
const SortableList: React.FC<SortableListProps> = ({
  value = [],
  onChange,
}) => {
  const { t } = useTranslation();
  
  /** Handle drag end event to reorder items */
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!active || !over) {
      return;
    }
    if (active.id !== over.id) {
      const activeIndex = value.findIndex((i) => i.key === active.id);
      const overIndex = value.findIndex((i) => i.key === over.id);
      console.log('onDragEnd', arrayMove([...value], activeIndex, overIndex))
      onChange?.(arrayMove([...value], activeIndex, overIndex));
    }
  };
  
  /** Listen to value changes and trigger callback */
  useEffect(() => {
    if (onChange) {
      onChange(value);
    }
  }, [value, onChange]);

  /** Handle input content change for a specific item */
  const inputChange = (e: React.ChangeEvent<HTMLInputElement>, index: number) => {
    const newItems = [...value];
    newItems[index].content = e.target.value;
    onChange?.(newItems);
  }
  
  /** Add new item to the list */
  const handleAdd = () => {
    onChange?.([...value, { key: Date.now(), content: '' }]);
  }

  return (
    <DndContext
      modifiers={[restrictToVerticalAxis]}
      onDragEnd={onDragEnd}
    >
      <SortableContext items={value.map((item) => item.key)} strategy={verticalListSortingStrategy}>
        <List
          dataSource={[...value, { type: 'add', key: Date.now(), content: '' }]}
          renderItem={(item: Item, index: number) => {
            console.log('renderItem', item, index)
            /** Render add button for special 'add' type */
            if (item.type === 'add') {
              return <Button block onClick={handleAdd}>{t('common.addOption')}</Button>
            } else {
              /** Render sortable item with drag handle and input */
              return (
                <SortableListItem key={item.key} itemKey={item.key}>
                  <DragHandle /> <Input variant="underlined" value={item.content} onChange={(e) => inputChange(e, index)} />
                </SortableListItem>
              )
            }}}
        />
      </SortableContext>
    </DndContext>
  );
};

export default SortableList;