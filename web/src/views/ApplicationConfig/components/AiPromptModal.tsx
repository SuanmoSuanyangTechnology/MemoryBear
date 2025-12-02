import { forwardRef, useImperativeHandle, useState } from 'react';
import { Row, Col, Space, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { AiPromptModalRef } from '../types'
// import { request } from '@/utils/request'
import RbModal from '@/components/RbModal'
import Markdown from '@/components/Markdown';

interface AiPromptModalProps {
  refresh: () => void;
}

const AiPromptModal = forwardRef<AiPromptModalRef, AiPromptModalProps>(({
  // refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [content, setContent] = useState('');


  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
  };

  const handleOpen = () => {
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  // const handleSave = () => {
  // }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('application.AIPromptAssistant')}
      open={visible}
      onCancel={handleClose}
      footer={null}
      width={1000}
    >
      <Row className="rb:rounded-[12px] rb:border rb:border-[#DFE4ED]">
        <Col span={12} className="rb:border-r rb:border-[#DFE4ED]">
          <div className="rb:p-[12px_17px] rb:border-b rb:border-[#DFE4ED]">{t('application.generatedPrompt')}</div>
          <div className="rb:h-[200px] rb:p-[16px]">
            <div className="rb:bg-[#F0F3F8] rb:h-full rb:w-full">
              <Markdown
                content={content}
              />
            </div>
          </div>
        </Col>
        <Col span={12}>
          <div className="rb:p-[12px_17px] rb:border-b rb:border-[#DFE4ED]">{t('application.conversationOptimizationPrompt')}</div>
          <div className="rb:h-[200px] rb:p-[16px]">
            <div className="rb:bg-[#F0F3F8] rb:h-full rb:w-full">
              
            </div>
          </div>
        </Col>
        <Col span={12} className="rb:border-r rb:border-[#DFE4ED]">
          <Space>
            <Button>{t('common.copy')}</Button>
            <Button type="primary">{t('common.apply')}</Button>
          </Space>
        </Col>
        <Col span={12}>
        </Col>
      </Row>
    </RbModal>
  );
});

export default AiPromptModal;