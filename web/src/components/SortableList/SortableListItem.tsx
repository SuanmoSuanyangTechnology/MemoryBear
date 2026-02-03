/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-11 20:42:28
 * @LastEditors: ZhaoYing
 * @LastEditTime: 2026-02-02 15:27:46
 */
/**
 * SortableListItem Component
 * 
 * A wrapper component that makes Ant Design List.Item draggable and sortable.
 * Integrates with @dnd-kit for drag-and-drop functionality.
 * 
 * @component
 */

import React, { useMemo } from 'react';
import {
  useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { List } from 'antd';
import type { GetProps } from 'antd';

import type { SortableListItemContextProps } from './types';
import SortableListItemContext from './SortableListItemContext';

/** Sortable list item component that wraps Ant Design List.Item with drag-and-drop functionality */
const SortableListItem: React.FC<GetProps<typeof List.Item> & { itemKey: number }> = (props) => {
  const { itemKey, style, ...rest } = props;

  /** Get sortable hooks and properties from @dnd-kit */
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: itemKey });

  /** Apply drag transform and transition styles */
  const listStyle: React.CSSProperties = {
    ...style,
    transform: CSS.Translate.toString(transform),
    transition,
    ...(isDragging ? { position: 'relative', zIndex: 9999 } : {}),
    display: 'flex',
    alignItems: 'center',
    borderBottom: 'none',
    padding: '8px 0',
  };

  /** Memoize context value to avoid unnecessary re-renders */
  const memoizedValue = useMemo<SortableListItemContextProps>(
    () => ({ setActivatorNodeRef, listeners, attributes }),
    [setActivatorNodeRef, listeners, attributes],
  );

  return (
    <SortableListItemContext.Provider value={memoizedValue}>
      <List.Item {...rest} ref={setNodeRef} style={listStyle} />
    </SortableListItemContext.Provider>
  );
};

export default SortableListItem;