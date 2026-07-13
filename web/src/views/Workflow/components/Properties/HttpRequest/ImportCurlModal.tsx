import { forwardRef, useImperativeHandle, useState } from 'react';
import { Input, message } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { parseCurl, type ParsedCurl } from './parseCurl'

export interface ImportCurlModalRef {
  handleOpen: () => void;
  handleClose: () => void;
}

interface ImportCurlModalProps {
  /** 解析成功后的回调，返回可回填表单的结构。 */
  onImport: (parsed: ParsedCurl) => void;
}

const ImportCurlModal = forwardRef<ImportCurlModalRef, ImportCurlModalProps>(({
  onImport,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [curl, setCurl] = useState('');

  const handleClose = () => {
    setVisible(false);
    setCurl('');
  };

  const handleOpen = () => {
    setCurl('');
    setVisible(true);
  };

  const handleSave = () => {
    if (!curl.trim()) {
      message.warning(t('workflow.config.http-request.curlEmpty'));
      return;
    }
    const { data: parsed, error } = parseCurl(curl);
    if (error || !parsed) {
      message.error(
        error
          ? t(error.key, error.values)
          : t('workflow.config.http-request.curlParseError'),
      );
      return;
    }
    onImport(parsed);
    message.success(t('workflow.config.http-request.curlImportSuccess'));
    handleClose();
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
  }));

  return (
    <RbModal
      title={t('workflow.config.http-request.importCurl')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Input.TextArea
        value={curl}
        onChange={(e) => setCurl(e.target.value)}
        placeholder={t('workflow.config.http-request.curlPlaceholder')}
        autoSize={{ minRows: 8, maxRows: 16 }}
      />
    </RbModal>
  );
});

export default ImportCurlModal;
