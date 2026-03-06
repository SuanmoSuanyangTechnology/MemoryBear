/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-28 14:08:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-06 12:05:46
 */
/**
 * UploadWorkflowModal Component
 * 
 * This component provides a modal for uploading workflow files with a multi-step process:
 * 1. Upload - Select platform and file
 * 2. Complex - Show warnings and errors if any
 * 3. SureInfo - Confirm and edit workflow information
 * 4. Completed - Show success message and options
 */
import { forwardRef, useImperativeHandle, useState, useMemo } from 'react';
import { Form, Select, Steps, Flex, Alert, Input, Button, Result, message } from 'antd';
import { useTranslation } from 'react-i18next';

import type { UploadWorkflowModalData, UploadData, UploadWorkflowModalRef } from '../types'
import RbModal from '@/components/RbModal'
import UploadFiles from '@/components/Upload/UploadFiles'
import { importWorkflow, completeImportWorkflow } from '@/api/application'

/**
 * Props for UploadWorkflowModal component
 */
interface UploadWorkflowModalProps {
  /** Function to refresh the parent component after workflow import */
  refresh: () => void;
}

/**
 * Steps definition for the upload process
 */
const steps = [
  'upload',      // Step 1: File upload
  'complex',     // Step 2: Error/warning display
  'sureInfo',    // Step 3: Information confirmation
  'completed'    // Step 4: Success message
]

/**
 * UploadWorkflowModal component
 * 
 * @param {UploadWorkflowModalProps} props - Component props
 * @param {React.Ref<UploadWorkflowModalRef>} ref - Ref for imperative methods
 */
const UploadWorkflowModal = forwardRef<UploadWorkflowModalRef, UploadWorkflowModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  
  // State management
  const [visible, setVisible] = useState(false);           // Modal visibility
  const [form] = Form.useForm<UploadWorkflowModalData>();  // Form instance
  const [loading, setLoading] = useState(false);           // Loading state
  const [current, setCurrent] = useState<number>(0);       // Current step
  const [data, setData] = useState<UploadData | null>(null); // Upload response data
  const [firstFormData, setFirstFormData] = useState<UploadWorkflowModalData | null>(null); // First step form data
  const [appId, setAppId] = useState<string | null>(null); // Imported application ID

  /**
   * Handle modal close
   * Resets all states and form fields
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setData(null);
    setCurrent(0);
    setFirstFormData(null);
    setAppId(null);
    setLoading(false);
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
        setFirstFormData(values);
        formData.append('platform', values.platform);
        formData.append('file', values.file[0]);

        // Call import workflow API
        importWorkflow(formData)
          .then(res => {
            const response = res as UploadData;
            const { errors, warnings } = response;
            setData(response);

            // Navigate to error/warning step if any, otherwise go to confirmation
            if (errors.length || warnings.length) {
              setCurrent(1);
            } else {
              setCurrent(2);
              // Pre-fill form with file information
              form.setFieldsValue({
                name: values.file[0].name.split('.')[0],
                platform: values.platform,
                fileName: values.file[0].name,
                fileSize: values.file[0].size,
              });
            }
          });
        break;
      case 1: // Step 2: Error/warning display
        if (firstFormData) {
          const { file, platform } = firstFormData;
          // Pre-fill form with file information
          form.setFieldsValue({
            name: file[0].name.split('.')[0],
            platform: platform,
            fileName: file[0].name,
            fileSize: file[0].size,
          });
        }
        setCurrent(2);
        break;
      case 2: // Step 3: Confirm information
        if (data) {
          // Complete import workflow
          completeImportWorkflow({
            temp_id: data.temp_id,
            name: values.name,
            description: values.description,
          })
            .then((res) => {
              const response = res as { id: string };
              setCurrent(3);
              setAppId(response.id);
            });
        }
        break;
      default:
        setCurrent(prev => prev + 1);
        break;
    }
  };

  // Expose methods to parent component via ref
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  /**
   * Handle navigation to previous step
   * Adjusts step based on whether there were errors/warnings
   */
  const handleLastStep = () => {
    let newStep = current - 1;
    // If no errors or warnings, skip the error/warning step
    if (!data?.warnings?.length && !data?.errors?.length) {
      newStep = current - 2;
    }

    // Reset form if not going back to error/warning step
    if (newStep !== 1) {
      form.resetFields();
    }
    setCurrent(newStep);
  };

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
    switch(current) {
      case 0: // Step 1: Upload
        return [
          <Button key="back" onClick={handleClose}>
            {t('common.cancel')}
          </Button>,
          <Button
            key="nextStep"
            type="primary"
            loading={loading}
            onClick={handleSave}
          >
            {t('common.nextStep')}
          </Button>
        ];
      case 3: // Step 4: Completed
        return null;
      default: // Steps 1-2
        return [
          <Button key="cancel" onClick={handleClose}>
            {t('common.cancel')}
          </Button>,
          <Button key="back" onClick={handleLastStep}>
            {t('common.prevStep')}
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={loading}
            onClick={handleSave}
          >
            {t('common.nextStep')}
          </Button>
        ];
    }
  }, [current]);

  return (
    <RbModal
      title={t('application.importThirdParty')}
      open={visible}
      onCancel={handleClose}
      okText={t('application.nextStep')}
      onOk={handleSave}
      footer={getFooter}
      width={1000}
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
      
      {/* Step 1: File upload */}
      {current === 0 &&
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            platform: 'dify'
          }}
        >
          <Form.Item
            name="platform" label={t('application.platform')}
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <Select
              placeholder={t('common.pleaseSelect')}
              options={['dify'].map(value => ({
                label: t(`application.${value}`), value: value,
              }))}
            />
          </Form.Item>
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
            />
          </Form.Item>
        </Form>
      }

      {/* Step 2: Error/warning display */}
      {current === 1 &&
        <Flex vertical gap={12} className="rb:w-[70%]! rb:mx-auto!">
          {data?.warnings.map(vo => (
            <Alert
              key={vo.node_id}
              message={<div>
                <div>{vo.node_name || vo.node_id} - {vo.type}</div>
                {vo.detail}
              </div>}
              type="warning"
              showIcon
            />
          ))}
          {data?.errors.map(vo => (
            <Alert
              key={vo.node_id}
              message={<div>
                <div>{vo.node_name || vo.node_id} - {vo.type}</div>
                {vo.detail}
              </div>}
              type="error"
              showIcon
            />
          ))}
        </Flex>
      }

      {/* Step 3: Information confirmation */}
      {current === 2 &&
        <Form form={form} layout="vertical" className="rb:w-[70%]! rb:mx-auto!">
          <div className="rb:text-[#5B6167] rb:font-medium">{t('application.baseInfo')}</div>
          <Form.Item name="name" label={t('application.workflowName')} rules={[{ required: true }]}>
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
          <Form.Item name="platform" label={t('application.platform')}>
            <Input disabled />
          </Form.Item>
          <Form.Item name="fileName" label={t('application.fileName')}>
            <Input disabled />
          </Form.Item>
          <Form.Item name="fileSize" label={t('application.fileSize')}>
            <Input disabled />
          </Form.Item>
          <Form.Item name="description" label={t('application.description')} layout="vertical">
            <Input.TextArea placeholder={t('common.pleaseEnter')} />
          </Form.Item>
        </Form>
      }
      
      {/* Step 4: Success message */}
      {current === 3 &&
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

export default UploadWorkflowModal;