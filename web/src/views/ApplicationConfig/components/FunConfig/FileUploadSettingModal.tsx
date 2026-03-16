/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-05 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-11 15:42:13
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Radio, InputNumber, Flex, Switch, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import RbModal from '@/components/RbModal';
import type { FunConfigForm } from '../../types'

interface FileUploadSettingModalRef {
  handleOpen: (values?: FileUploadSettings) => void;
  handleClose: () => void;
}

interface FileUploadSettings extends Omit<FunConfigForm, 'enabled'> {} 

interface FileUploadSettingModalProps {
  onSave: (values: FileUploadSettings) => void;
}

const fileTypeOptions = [
  {
    type: 'document',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/txt.svg')]"></div>,
    formats: 'TXT, MD, MDX, MARKDOWN, PDF, DOC, DOCX',
    defaultMaxCount: 1,
    defaultMaxSize: 2
  },
  {
    type: 'image',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/image.svg')]"></div>,
    formats: 'JPG, JPEG, PNG, GIF, WEBP, SVG',
    defaultMaxCount: 1,
    defaultMaxSize: 2
  },
  {
    type: 'audio',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/audio.svg')]"></div>,
    formats: 'MP3, M4A, WAV, AMR, MPGA',
    defaultMaxCount: 1,
    defaultMaxSize: 2
  },
  {
    type: 'video',
    icon: <div className="rb:size-9 rb:bg-cover rb:bg-[url('@/assets/images/file/video.svg')]"></div>,
    formats: 'MP4, MOV, MPEG, WEBM',
    defaultMaxCount: 1,
    defaultMaxSize: 2
  },
];

const FileUploadSettingModal = forwardRef<FileUploadSettingModalRef, FileUploadSettingModalProps>(({
  onSave,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const values = Form.useWatch([], form)

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = (values?: FileUploadSettings) => {
    setVisible(true);
    // if (values) {
    //   form.setFieldsValue(values);
    // }
  };

  const handleSave = async () => {
    const values = await form.validateFields();
    onSave(values);
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
      width={600}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          uploadType: 'both',
          fileTypes: fileTypeOptions.map(opt => ({
            type: opt.type,
            enabled: false,
            maxCount: opt.defaultMaxCount,
            maxSize: opt.defaultMaxSize
          }))
        }}
      >
        <Form.Item
          label={t('application.uploadType')}
          name="uploadType"
        >
          <Radio.Group block buttonStyle="solid">
            <Radio.Button value="local">{t('application.local')}</Radio.Button>
            <Radio.Button value="url">URL</Radio.Button>
            <Radio.Button value="both">{t('application.both')}</Radio.Button>
          </Radio.Group>
        </Form.Item>
        <div className="rb:text-[12px] rb:text-[#5B6167] rb:mb-1">{t('application.maxCount')}</div>
        <Form.Item
          name="maxCount"
          label={t('application.maxCount')}
        >
          <InputNumber min={1} max={100} className="rb:w-full!" placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        <Form.Item label={t('application.supportedTypes')}>
          <Form.List name="fileTypes">
            {(fields) => (
              <Flex vertical gap={12}>
                {fields.map((field, index) => {
                  const option = fileTypeOptions[index];
                  const isEnabled = values?.fileTypes?.[index]?.enabled;
                  
                  return (
                    <div
                      key={field.key}
                      className={clsx("rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-3", {
                        'rb:bg-[#f5f7fc]': isEnabled
                      })}
                    >
                      <Row gutter={12}>
                        <Col flex="36px" className="rb:self-center">
                          {option.icon}
                        </Col>
                        <Col flex="1">
                          <Flex align="center" justify="space-between">
                            <Flex vertical>
                              <div className="rb:font-medium">{t(`application.${option.type}`)}</div>
                              <div className="rb:text-[12px] rb:text-[#5B6167]">{option.formats}</div>
                            </Flex>
                            <Form.Item name={[field.name, 'enabled']} valuePropName="checked" noStyle>
                              <Switch />
                            </Form.Item>
                          </Flex>
                        </Col>
                      </Row>
                      {isEnabled && (
                        <Flex align="center" gap={12} className="rb:mt-3! rb:pt-3! rb:border-t rb:border-[#DFE4ED]">
                          <div>{t('application.singleMaxSize')}: </div>
                          <Form.Item name={[field.name, 'maxSize']} noStyle>
                            <InputNumber min={1} max={500} suffix="MB" className="rb:flex-1" />
                          </Form.Item>
                        </Flex>
                      )}
                      <Form.Item name={[field.name, 'type']} hidden>
                        <input type="hidden" />
                      </Form.Item>
                    </div>
                  );
                })}
              </Flex>
            )}
          </Form.List>
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default FileUploadSettingModal;
