/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:27:25 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:27:25 
 */
/**
 * DragHandle Component
 * 
 * A drag handle button for sortable list items.
 * Uses the HolderOutlined icon and connects to the sortable context.
 * 
 * @component
 */

import React, { useContext } from 'react';
import { HolderOutlined } from '@ant-design/icons';
import { Button } from 'antd';

import SortableListItemContext from './SortableListItemContext';

/** Drag handle component for sortable list items */
const DragHandle: React.FC = () => {
  const { setActivatorNodeRef, listeners, attributes } = useContext(SortableListItemContext);
  return (
    <Button
      type="text"
      size="small"
      icon={<HolderOutlined />}
      style={{ cursor: 'move' }}
      ref={setActivatorNodeRef}
      {...attributes}
      {...listeners}
    />
  );
};

export default DragHandle;