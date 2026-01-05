import { useRef } from 'react';
import { Button } from 'antd';
import CreateContentModal from './CreateContentModal';
import type { CreateContentModalRef } from '../types';

// 使用示例组件
const CreateContentModalExample = () => {
  const createContentModalRef = useRef<CreateContentModalRef>(null);

  const handleOpenModal = () => {
    // 打开弹窗，传入知识库ID和父级ID
    createContentModalRef.current?.handleOpen('kb_123', 'parent_456');
  };

  const handleRefreshTable = () => {
    console.log('刷新表格数据');
    // 这里可以添加刷新表格的逻辑
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