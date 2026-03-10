/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-28 14:08:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-06 12:05:46
 */
/**
 * UploadModal Component
 * 
 * This component provides a modal for uploading workflow files with a multi-step process:
 * 1. Upload - Select platform and file
 * 2. Complex - Show warnings and errors if any
 * 3. SureInfo - Confirm and edit workflow information
 * 4. Completed - Show success message and options
 */
import { forwardRef, useImperativeHandle, useState, useMemo } from 'react';
import { Form, Steps, Flex, Alert, Button, Result, message } from 'antd';
import { useTranslation } from 'react-i18next';

import type { Application, UploadModalRef } from '../types'
import RbModal from '@/components/RbModal'
import UploadFiles from '@/components/Upload/UploadFiles'
import { appImport } from '@/api/application'

/**
 * Props for UploadModal component
 */
interface UploadModalProps {
  /** Function to refresh the parent component after workflow import */
  refresh: () => void;
}


/**
 * Steps definition for the upload process
 */
const steps = [
  'upload',      // Step 1: File upload
  'complex',     // Step 2: Error/warning display
  'completed'    // Step 4: Success message
]
/**
 * UploadModal component
 * 
 * @param {UploadModalProps} props - Component props
 * @param {React.Ref<UploadModalRef>} ref - Ref for imperative methods
 */
const UploadModal = forwardRef<UploadModalRef, UploadModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  
  // State management
  const [visible, setVisible] = useState(false);           // Modal visibility
  const [form] = Form.useForm<{ file: File[] }>();  // Form instance
  const [loading, setLoading] = useState(false);           // Loading state
  const [current, setCurrent] = useState<number>(0);       // Current step
  const [appId, setAppId] = useState<string | null>(null); // Imported application ID
  const [warnings, setWarnings] = useState<string[]>([])

  /**
   * Handle modal close
   * Resets all states and form fields
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setCurrent(0);
    setAppId(null);
    setLoading(false);
    setWarnings([])
  };

  /**
   * Handle modal open
   * Resets form fields and shows modal
   */
  const handleOpen = () => {
    form.resetFields();
    setVisible(true);
  };

  /**
   * Handle save/submit action
   * Processes different logic based on current step
   */
  const handleSave = () => {
    const values = form.getFieldsValue();
    
    switch(current) {
      case 0: // Step 1: Upload file
        if (!values.file || values.file.length === 0) {
          message.warning(t('application.pleaseUploadFile'));
          return;
        }
        const formData = new FormData();
        formData.append('file', values.file[0]);

        setLoading(true)
        // Call import API
        appImport(formData)
          .then(res => {
            const { warnings, app } = res as { warnings: string[]; app: Application };

            setAppId(app?.id)
            if (warnings.length) {
              setCurrent(1)
              setWarnings(warnings)
            } else {
              setCurrent(2)
            }
          })
          .finally(() => setLoading(false));
        break;
      case 2:
        break;
    }
  };

  // Expose methods to parent component via ref
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  /**
   * Handle navigation after successful import
   * @param {string} type - Navigation type ('detail' or 'list')
   */
  const handleJump = (type: string) => {
    handleClose();
    refresh();
    setTimeout(() => {
      switch (type) {
        case 'detail':
          // Open application detail page in new tab
          window.open(`/#/application/config/${appId}`, '_blank');
          break;
      }
    }, 100)
  };

  /**
   * Generate modal footer based on current step
   */
  const getFooter = useMemo(() => {
    switch (current) {
      case 0: // Step 1: Upload
        return [
          <Button key="back" onClick={handleClose}>
            {t('common.cancel')}
          </Button>,
          <Button
            key="confirm"
            type="primary"
            loading={loading}
            onClick={handleSave}
          >
            {t('common.confirm')}
          </Button>
        ];
      case 1:
        return [
          <Button key="back" onClick={() => handleJump('list')}>
            {t('application.gotoList')}
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={loading}
            onClick={() => handleJump('detail')}
          >
            {t('application.gotoDetail')}
          </Button>
        ]
      default:
        return null;
    }
  }, [current, loading]);
  return (
    <RbModal
      title={t('application.import')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.confirm')}
      onOk={handleSave}
      footer={getFooter}
    >
      {/* Steps indicator */}
      <div className='rb:p-3 rb:bg-[#FBFDFF] rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:mb-3'>
        <Steps
          labelPlacement="vertical"
          size="small"
          current={current}
          items={steps.map(key => ({ title: t(`application.${key}`) }))}
        />
      </div>
      {current === 0 &&
        <Form
          form={form}
          layout="vertical"
        >
          <Form.Item
            name="file"
            valuePropName="fileList"
            noStyle
          >
            <UploadFiles
              isAutoUpload={false}
              isCanDrag={true}
              fileSize={100}
              maxCount={1}
              fileType={['yml']}
              draggerHeight={200}
            />
          </Form.Item>
        </Form>
      }
      {/* Step 2: Error/warning display */}
      {current === 1 &&
        <Flex vertical gap={12}>
          {warnings.map((vo, index) => (
            <Alert
              key={index}
              message={<div>{vo}</div>}
              type="warning"
              showIcon
            />
          ))}
        </Flex>
      }
      {current === 2 &&
        <Result
          status="success"
          title={t('application.importSuccess')}
          subTitle={t('application.importSuccessDesc')}
          extra={[
            <Button key="back" onClick={() => handleJump('list')}>
              {t('application.gotoList')}
            </Button>,
            <Button
              key="submit"
              type="primary"
              loading={loading}
              onClick={() => handleJump('detail')}
            >
              {t('application.gotoDetail')}
            </Button>
          ]}
        />
      }
    </RbModal>
  );
});

export default UploadModal;