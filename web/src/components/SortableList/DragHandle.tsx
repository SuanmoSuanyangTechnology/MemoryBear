import React, { useContext } from 'react';
import { HolderOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import SortableListItemContext from './SortableListItemContext';

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