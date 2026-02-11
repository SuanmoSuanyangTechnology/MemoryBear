import { useRef } from 'react';
import { Button } from 'antd';
import CreateContentModal from './CreateContentModal';
import type { CreateContentModalRef } from '../types';

// Example usage component
const CreateContentModalExample = () => {
  const createContentModalRef = useRef<CreateContentModalRef>(null);

  const handleOpenModal = () => {
    // Open modal, pass knowledge base ID and parent ID
    createContentModalRef.current?.handleOpen('kb_123', 'parent_456');
  };

  const handleRefreshTable = () => {
    console.log('Refresh table data');
    // Add table refresh logic here
  };

  return (
    <div>
      <Button type="primary" onClick={handleOpenModal}>
        创建内容
      </Button>
      
      <CreateContentModal
        ref={createContentModalRef}
        refreshTable={handleRefreshTable}
      />
    </div>
  );
};

export default CreateContentModalExample;