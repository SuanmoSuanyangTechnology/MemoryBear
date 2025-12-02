import { forwardRef, useImperativeHandle, useState } from 'react';
import { Input, message, Tabs } from 'antd';
import { useTranslation } from 'react-i18next';
import RbModal from '@/components/RbModal';
import RbMarkdown from '@/components/Markdown';

const { TextArea } = Input;

export interface InsertModalRef {
  handleOpen: (documentId: string, initialContent?: string, chunkId?: string) => void;
  handleClose: () => void;
}

interface InsertModalProps {
  onInsert?: (documentId: string, content: string, chunkId?: string) => Promise<boolean>;
  onSuccess?: () => void;
}

const InsertModal = forwardRef<InsertModalRef, InsertModalProps>(({ onInsert, onSuccess }, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [documentId, setDocumentId] = useState<string>('');
  const [content, setContent] = useState<string>('');
  const [chunkId, setChunkId] = useState<string | undefined>(undefined);
  const [isEditMode, setIsEditMode] = useState(false);
  const [activeTab, setActiveTab] = useState<string>('edit');

  const handleOpen = (docId: string, initialContent?: string, chunkIdParam?: string) => {
    setDocumentId(docId);
    setContent(initialContent || '');
    setChunkId(chunkIdParam);
    setIsEditMode(!!initialContent);
    setVisible(true);
  };

  const handleClose = () => {
    setVisible(false);
    setContent('');
    setDocumentId('');
    setChunkId(undefined);
    setIsEditMode(false);
    setActiveTab('edit');
  };

  const handleOk = async () => {
    if (!content.trim()) {
      message.warning(t('knowledgeBase.pleaseEnterContent') || '请输入内容');
      return;
    }

    if (!documentId) {
      message.error(t('knowledgeBase.documentIdRequired') || '文档ID不能为空');
      return;
    }

    setLoading(true);
    try {
      if (onInsert) {
        const success = await onInsert(documentId, content.trim(), chunkId);
        if (success) {
          const successMsg = isEditMode 
            ? (t('knowledgeBase.updateSuccess') || '更新成功')
            : (t('knowledgeBase.insertSuccess') || '插入成功');
          message.success(successMsg);
          handleClose();
          // 只有插入模式才调用 onSuccess（编辑模式已在 handleInsertContent 中直接更新列表）
          if (!isEditMode) {
            onSuccess?.();
          }
        } else {
          const errorMsg = isEditMode
            ? (t('knowledgeBase.updateFailed') || '更新失败')
            : (t('knowledgeBase.insertFailed') || '插入失败');
          message.error(errorMsg);
        }
      }
    } catch (error) {
      console.error('操作失败:', error);
      const errorMsg = isEditMode
        ? (t('knowledgeBase.updateFailed') || '更新失败')
        : (t('knowledgeBase.insertFailed') || '插入失败');
      message.error(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setContent(e.target.value);
  };

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
  }));

  // 构建标签页项目，content 为空或新增时不显示预览
  const tabItems = [
    {
      key: 'edit',
      label: t('knowledgeBase.edit') || '编辑',
      children: (
        <TextArea
          value={content}
          onChange={handleContentChange}
          placeholder={t('knowledgeBase.insertContentPlaceholder') || '请输入内容...'}
          rows={10}
          maxLength={10000}
          showCount
          autoFocus
        />
      ),
    },
  ];

  // 只有在编辑模式且有内容时才显示预览标签页
  if (isEditMode && content) {
    tabItems.push({
      key: 'preview',
      label: t('knowledgeBase.preview') || '预览',
      children: (
        <div className='rb:border rb:border-[#D9D9D9] rb:rounded rb:p-4 rb:min-h-[280px] rb:max-h-[400px] rb:overflow-y-auto rb:bg-white'>
          <RbMarkdown content={content} showHtmlComments={true} />
        </div>
      ),
    });
  }

  return (
    <RbModal
      title={isEditMode 
        ? (t('knowledgeBase.editContent') || '编辑内容')
        : (t('knowledgeBase.insertContent') || '插入内容')
      }
      open={visible}
      onCancel={handleClose}
      onOk={handleOk}
      confirmLoading={loading}
      okText={t('common.confirm') || '确认'}
      cancelText={t('common.cancel') || '取消'}
      width={600}
    >
      <div className='rb:flex rb:flex-col rb:gap-4'>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
        />
      </div>
    </RbModal>
  );
});

export default InsertModal;
