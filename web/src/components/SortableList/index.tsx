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

interface Item {
  key: number;
  content: string;
  type?: 'add';
}
interface SortableListProps {
  value?: Item[];
  onChange?: (items?: Item[]) => void;
}

const SortableList: React.FC<SortableListProps> = ({
  value = [],
  onChange,
}) => {
  const { t } = useTranslation();
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
  // 监听value变化，包括初始值
  useEffect(() => {
    if (onChange) {
      onChange(value);
    }
  }, [value, onChange]);

  const inputChange = (e: React.ChangeEvent<HTMLInputElement>, index: number) => {
    const newItems = [...value];
    newItems[index].content = e.target.value;
    onChange?.(newItems);
  }
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
            if (item.type === 'add') {
              return <Button block onClick={handleAdd}>{t('common.addOption')}</Button>
            } else {
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