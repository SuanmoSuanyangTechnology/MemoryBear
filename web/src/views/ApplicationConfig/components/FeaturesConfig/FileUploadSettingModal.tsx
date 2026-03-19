/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-05 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-19 20:19:14
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, InputNumber, Flex, Switch, Row, Col, Radio } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import RbModal from '@/components/RbModal';
import type { FeaturesConfigForm } from '../../types'

type FileUpload = Omit<FeaturesConfigForm['file_upload'], 'settings'>

interface FileUploadSettingModalRef {
  handleOpen: (values?: FileUpload) => void;
  handleClose: () => void;
}

interface FileUploadSettingModalProps {
  onSave: (values: FileUpload) => void;
}

const fileTypeOptions = [
  {
    type: 'document',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/txt.svg')]"></div>,
    formats: [
      "pdf",
      "docx",
      "doc",
      "xlsx",
      "xls",
      "txt",
      "csv",
      "json",
      "md",
    ],
  },
  {
    type: 'image',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/image.svg')]"></div>,
    formats: [
      "png",
      "jpg",
      "jpeg"
    ],
  },
  {
    type: 'audio',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/audio.svg')]"></div>,
    formats: [
      "mp3",
      "wav",
      "m4a",
    ],
  },
  {
    type: 'video',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/video.svg')]"></div>,
    formats: [
      "mp4",
      "mov",
    ],
  },
];

const defaultValues: FileUpload = {
  enabled: false,
  image_enabled: false,
  image_max_size_mb: 20,
  image_allowed_extensions: [
    "png",
    "jpg",
    "jpeg"
  ],
  audio_enabled: false,
  audio_max_size_mb: 50,
  audio_allowed_extensions: [
    "mp3",
    "wav",
    "m4a",
  ],
  document_enabled: false,
  document_max_size_mb: 100,
  document_allowed_extensions: [
    "pdf",
    "docx",
    "doc",
    "xlsx",
    "xls",
    "txt",
    "csv",
    "json",
    "md",
  ],
  video_enabled: false,
  video_max_size_mb: 100,
  video_allowed_extensions: [
    "mp4",
    "mov",
  ],
  max_file_count: 5,
  allowed_transfer_methods: 'both'
}

const FileUploadSettingModal = forwardRef<FileUploadSettingModalRef, FileUploadSettingModalProps>(({
  onSave,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<FileUpload>();
  const values = Form.useWatch([], form)

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = (values?: FileUpload) => {
    setVisible(true);
    if (values) {
      const methods = values.allowed_transfer_methods || ['local_file', 'remote_url']
      const transferMethod = Array.isArray(methods)
        ? methods.length === 2 ? 'both' : methods[0]
        : methods
      form.setFieldsValue({ ...values, allowed_transfer_methods: transferMethod as any })
    } else {
      form.setFieldsValue(defaultValues)
    }
  };

  const handleSave = async () => {
    const vals = await form.validateFields();
    const methodMap: Record<string, string[]> = {
      local_file: ['local_file'],
      remote_url: ['remote_url'],
      both: ['local_file', 'remote_url'],
    }
    onSave({ ...vals, allowed_transfer_methods: methodMap[vals.allowed_transfer_methods as unknown as string] ?? [] });
    handleClose();
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.settings')}
      open={visible}
      onCancel={handleClose}
      onOk={handleSave}
    >
      <Form form={form} layout="vertical" initialValues={defaultValues}>
        <Form.Item
          label={t('application.uploadType')}
          name="allowed_transfer_methods"
        >
          <Radio.Group block buttonStyle="solid">
            <Radio.Button value="local_file">{t('application.local')}</Radio.Button>
            <Radio.Button value="remote_url">URL</Radio.Button>
            <Radio.Button value="both">{t('application.both')}</Radio.Button>
          </Radio.Group>
        </Form.Item>

        <div className="rb:text-[12px] rb:text-[#5B6167] rb:mb-1">{t('application.maxCount')}</div>
        <Form.Item label={t('application.maxCount')} name="max_file_count">
          <InputNumber min={1} max={20} precision={0} className="rb:w-full!" placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item label={t('application.supportedTypes')}>
          <Flex vertical gap={12}>
            {fileTypeOptions.map((option) => {
              const enabledKey = `${option.type}_enabled` as keyof FileUpload
              const sizeKey = `${option.type}_max_size_mb` as keyof FileUpload
              const isEnabled = values?.[enabledKey]
              return (
                <div
                  key={option.type}
                  className={clsx('rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-3', {
                    'rb:bg-[#f5f7fc]': isEnabled
                  })}
                >
                  <Row gutter={12}>
                    <Col flex="36px" className="rb:self-center">{option.icon}</Col>
                    <Col flex="1">
                      <Flex align="center" justify="space-between">
                        <Flex vertical>
                          <div className="rb:font-medium">{t(`application.${option.type}`)}</div>
                          <div className="rb:text-[12px] rb:text-[#5B6167]">{option.formats.map(item => item.toUpperCase()).join(', ')}</div>
                        </Flex>
                        <Form.Item name={enabledKey} valuePropName="checked" noStyle>
                          <Switch />
                        </Form.Item>
                      </Flex>
                    </Col>
                  </Row>
                  {isEnabled && (
                    <Flex align="center" gap={12} className="rb:mt-3! rb:pt-3! rb:border-t rb:border-[#DFE4ED]">
                      <div>{t('application.singleMaxSize')}: </div>
                      <Form.Item name={sizeKey} noStyle>
                        <InputNumber min={1} max={100} suffix="MB" className="rb:flex-1" />
                      </Form.Item>
                      <Form.Item name={`${option.type}_allowed_extensions`} hidden />
                    </Flex>
                  )}
                </div>
              )
            })}
          </Flex>
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default FileUploadSettingModal;
