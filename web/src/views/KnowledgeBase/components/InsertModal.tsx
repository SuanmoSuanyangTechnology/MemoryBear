import { forwardRef, useImperativeHandle, useState } from 'react';
import { message, Tabs } from 'antd';
import { useTranslation } from 'react-i18next';
import RbModal from '@/components/RbModal';
import RbMarkdown from '@/components/Markdown';
import './index.css'

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
  const [activeTab, setActiveTab] = useState<string>('normalMode');
  const [mode, setMode] = useState(0); // 0: 普通模式, 1: QA模式
  const [question, setQuestion] = useState<string>('');
  const [answer, setAnswer] = useState<string>('');

  const handleOpen = (docId: string, initialContent?: string, chunkIdParam?: string) => {
    setDocumentId(docId);
    handleContent(initialContent || '')
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
    setActiveTab('normalMode');
    setMode(0);
    setQuestion('');
    setAnswer('');
  };

  // 解析 QA 格式内容
  const parseQAContent = (content: string) => {
    if (!content) return null;
    
    const qaRegex = /question:\s*(.*?)\s*answer:\s*(.*?)$/s;
    const match = content.match(qaRegex);
    
    if (match) {
      const question = match[1]?.trim() || '';
      const answer = match[2]?.trim() || '';
      return { question, answer };
    }
    
    return null;
  };

  const handleContent = (value: string) => {
    if (value === '') return;
    const qaContent = parseQAContent(value);
    if (qaContent) {
      setMode(1); // 1 表示 QA 模式
      setQuestion(qaContent.question);
      setAnswer(qaContent.answer);
      setContent(qaContent.answer); // 保持原始内容用于提交
      setActiveTab('qaMode')
    } else {
      setMode(0);
      setAnswer(value)
      setContent(value);
      setActiveTab('normalMode')
    }
  };
  const handleTabsChange = (key: string) => {
    if(key === 'qaMode'){
      setMode(1);
    }else{
      setMode(0);
    }
    setActiveTab(key);
  };
  // 获取当前要提交的内容
  const getCurrentContent = () => {
    if (mode === 1) {
      return `question: ${question}\n answer: ${answer}`;
    }
    return content;
  };

  const handleOk = async () => {
    const currentContent = getCurrentContent();
    if (!currentContent.trim()) {
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
        const success = await onInsert(documentId, currentContent.trim(), chunkId);
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

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
  }));

  // 构建标签页项目
  const tabItems = [
      {
      key: 'normalMode',
      label: t('knowledgeBase.normalMode'),
      children: (
        // <div className='rb:border rb:border-[#D9D9D9] rb:rounded rb:p-4 rb:min-h-[280px] rb:max-h-[400px] rb:overflow-y-auto rb:bg-white'>
          <RbMarkdown 
            content={content} 
            showHtmlComments={true} 
            editable={true}
            onContentChange={setContent}
            onSave={(newContent) => {
              setContent(newContent);
            }}
          />
        // </div>
      ),
    },
    {
      key: 'qaMode',
      label: t('knowledgeBase.qaMode'),
      children: (
        // QA 模式的编辑界面
        <div className='rb:flex rb:flex-col rb:gap-4'>
          <div>
            <div className='rb:w-full rb:font-medium rb:leading-8 rb:mb-2'>{t('knowledgeBase.question') || '问题'}</div>
            {/* <div className='rb:border rb:border-[#D9D9D9] rb:rounded rb:p-4 rb:min-h-[120px] rb:max-h-[200px] rb:overflow-y-auto rb:bg-white'> */}
              <RbMarkdown 
                content={question} 
                showHtmlComments={true} 
                editable={true}
                onContentChange={setQuestion}
                onSave={(newContent) => {
                  setQuestion(newContent);
                }}
              />
            {/* </div> */}
          </div>
          <div>
            <div className='rb:w-full rb:font-medium rb:leading-8 rb:mb-2'>{t('knowledgeBase.answer') || '答案'}</div>
            {/* <div className='rb:border rb:border-[#D9D9D9] rb:rounded rb:p-4 rb:min-h-[120px] rb:max-h-[200px] rb:overflow-y-auto rb:bg-white'> */}
              <RbMarkdown 
                content={answer} 
                showHtmlComments={true} 
                editable={true}
                onContentChange={setAnswer}
                onSave={(newContent) => {
                  setAnswer(newContent);
                }}
              />
            {/* </div> */}
          </div>
        </div>
      ) 
    }

  ];

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
      className='rb:h-[800px]'
    >
      <div className='rb:flex rb:flex-col rb:gap-4'>
        <Tabs
          activeKey={activeTab}
          onChange={handleTabsChange}
          items={tabItems}
        />
      </div>
    </RbModal>
  );
});

export default InsertModal;