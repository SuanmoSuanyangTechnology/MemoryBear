/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:09:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-09 10:17:54
 */
/**
 * Upload File List Modal Component
 * 
 * A modal dialog for adding remote files via URL.
 * Allows users to specify file type and URL for files hosted externally.
 * 
 * Features:
 * - Dynamic form fields for multiple file URLs
 * - File type selection (currently supports images)
 * - Form validation
 * - Add/remove file entries
 * 
 * @component
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { UploadFileListModalRef } from '../types'
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

interface UploadFileListModalProps {
  /** Callback to refresh parent component with new file list */
  refresh: (fileList?: any[]) => void;
}

/**
 * Modal for adding remote files via URL
 */
const UploadFileListModal = forwardRef<UploadFileListModalRef, UploadFileListModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false)

  /**
   * Closes the modal and resets loading state
   */
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
  };

  /**
   * Opens the modal and resets form fields
   */
  const handleOpen = () => {
    setVisible(true);
    form.resetFields();
  };
  /**
   * Validates and saves the file list
   * Transforms form values into file objects with transfer_method: 'remote_url'
   */
  const handleSave = () => {
    form.validateFields().then((values) => {
      const fileList = values.files?.map((file: any) => ({
        ...file,
        uid: Math.random().toString(36).substr(2, 9),
        transfer_method: 'remote_url'
      })) || [];
      refresh(fileList)
      handleClose()
    })
  }

  // Expose methods to parent component via ref
  useImperativeHandle(ref, () => ({
    handleOpen
  }));

  return (
    <RbModal
      title={t('memoryConversation.addRemoteFile')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form form={form} layout="vertical">
        <Form.List name="files">
          {(fields, { add, remove }) => (
            <>
              {/* Render each file entry with type selector and URL input */}
              {fields.map(({ key, name, ...restField }) => (
                <Flex key={key} gap={8} align="center" className="rb:mb-3!">
                  <FormItem
                    {...restField}
                    name={[name, 'type']}
                    initialValue="image"
                    className="rb:mb-0!"
                  >
                    <Select
                      placeholder={t('memoryConversation.fileType')}
                      options={[
                        { label: t('memoryConversation.image'), value: 'image' }
                      ]}
                      className="rb:w-30"
                    />
                  </FormItem>
                  <FormItem
                    {...restField}
                    name={[name, 'url']}
                    rules={[{ required: true, message: t('common.pleaseEnter') }]}
                    className="rb:mb-0!"
                  >
                    <Input placeholder={t('memoryConversation.fileUrl')} className="rb:w-82.5!" />
                  </FormItem>
                  <div
                    className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]"
                    onClick={() => remove(name)}
                  ></div>
                </Flex>
              ))}
              <Form.Item noStyle>
                <Button type="dashed" onClick={() => add()} block>
                  + {t('common.add')}
                </Button>
              </Form.Item>
            </>
          )}
        </Form.List>
      </Form>
    </RbModal>
  );
});

export default UploadFileListModal;