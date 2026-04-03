/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-30 13:59:36 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-02 19:01:12
 */
import { forwardRef, useImperativeHandle, useState, useRef, useMemo } from 'react';
import { Form, Input, Select, InputNumber, Button, Row, Col, Flex, Spin } from 'antd';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import type { ChatVariableModalRef } from './types'
import type { ChatVariable } from '../../types';
import RbModal from '@/components/RbModal'
import { defaultValues as defaultFileUploadValues } from '@/views/ApplicationConfig/components/FeaturesConfig/FileUploadSettingModal'
import UploadFiles from '@/views/Conversation/components/FileUpload'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import type { UploadFileListModalRef } from '@/views/Conversation/types'
import { getFileInfoByUrl } from '@/api/fileStorage'
import { transform_file_type } from '@/views/Conversation/components/FileUpload'

const FormItem = Form.Item;

interface ChatVariableModalProps {
  refresh: (value: ChatVariable, editIndex?: number) => void;
}

const types = [
  'string',
  'number',
  'boolean',
  'object',
  'file',
  'array[file]',
  'array[string]',
  'array[number]',
  'array[boolean]',
  'array[object]',
]

const ChatVariableModal = forwardRef<ChatVariableModalRef, ChatVariableModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null);

  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ChatVariable>();
  const [loading, setLoading] = useState(false);
  const [fileList, setFileList] = useState<any[]>([]);
  const [editIndex, setEditIndex] = useState<number | undefined>(undefined);

  const type = Form.useWatch('type', form);
  const allowed_transfer_methods = Form.useWatch('allowed_transfer_methods', form);
  const image_enabled = Form.useWatch('image_enabled', form);
  const audio_enabled = Form.useWatch('audio_enabled', form);
  const document_enabled = Form.useWatch('document_enabled', form);
  const video_enabled = Form.useWatch('video_enabled', form);
  const image_max_size_mb = Form.useWatch('image_max_size_mb', form);
  const audio_max_size_mb = Form.useWatch('audio_max_size_mb', form);
  const document_max_size_mb = Form.useWatch('document_max_size_mb', form);
  const video_max_size_mb = Form.useWatch('video_max_size_mb', form);
  const image_allowed_extensions = Form.useWatch('image_allowed_extensions', form);
  const audio_allowed_extensions = Form.useWatch('audio_allowed_extensions', form);
  const document_allowed_extensions = Form.useWatch('document_allowed_extensions', form);
  const video_allowed_extensions = Form.useWatch('video_allowed_extensions', form);
  const max_file_count = Form.useWatch('max_file_count', form);

  const hasEnabledFileType = !!(image_enabled || audio_enabled || document_enabled || video_enabled);

  const featureConfig = useMemo(() => ({
    enabled: hasEnabledFileType,
    allowed_transfer_methods,
    max_file_count,
    image_enabled, image_max_size_mb, image_allowed_extensions,
    audio_enabled, audio_max_size_mb, audio_allowed_extensions,
    document_enabled, document_max_size_mb, document_allowed_extensions,
    video_enabled, video_max_size_mb, video_allowed_extensions,
  }), [
    hasEnabledFileType, allowed_transfer_methods, max_file_count,
    image_enabled, image_max_size_mb, image_allowed_extensions,
    audio_enabled, audio_max_size_mb, audio_allowed_extensions,
    document_enabled, document_max_size_mb, document_allowed_extensions,
    video_enabled, video_max_size_mb, video_allowed_extensions,
  ]);

  const handleClose = () => {
    setFileList([]);
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditIndex(undefined);
  };

  const handleOpen = (variable?: ChatVariable, index?: number) => {
    setVisible(true);
    if (variable) {
      const { default: _, ...rest } = variable;
      form.setFieldsValue({ ...rest });
      setEditIndex(index);
      if (variable.type === 'file' || variable.type === 'array[file]') {
        const defaultVal = variable.defaultValue;
        if (defaultVal) {
          const list = Array.isArray(defaultVal) ? defaultVal : [defaultVal];
          setFileList(list);
        }
      }
    } else {
      form.resetFields();
      setEditIndex(undefined);
    }
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      refresh({ ...values, default: values.defaultValue }, editIndex);
      handleClose();
    });
  };

  useImperativeHandle(ref, () => ({ handleOpen }));

  const setFormFileValue = (updated: any[]) => {
    const isSingle = form.getFieldValue('type') === 'file';
    form.setFieldValue('defaultValue', isSingle ? (updated[0] ?? null) : updated);
  };

  const fileChange = (file?: any) => {
    const fileObj = file ? {
      ...file,
      type: file.type,
      transfer_method: "local_file",
      upload_file_id: file.response?.data?.file_id,
    } : undefined
    if (form.getFieldValue('type') === 'file') {
      const updated = [fileObj];
      setFileList(updated);
      setTimeout(() => setFormFileValue(updated), 0);
      return;
    }
    setFileList(prev => {
      const index = prev.findIndex((item: any) => item.uid === fileObj.uid);
      const updated = index > -1
        ? prev.map((item, i) => i === index ? fileObj : item)
        : [...prev, fileObj];
      setTimeout(() => setFormFileValue(updated), 0);
      return updated;
    });
  };

  const addFileList = (list?: any[]) => {
    if (!list?.length) return;
    const uploadingList = list.map(f => ({ ...f, status: 'uploading' }));
    setFileList(prev => {
      const isSingle = form.getFieldValue('type') === 'file';
      const updated = isSingle ? [uploadingList[0]] : [...prev, ...uploadingList];
      setTimeout(() => setFormFileValue(updated), 0);
      return updated;
    });
    const isSingle = form.getFieldValue('type') === 'file';
    (isSingle ? [uploadingList[0]] : uploadingList).forEach(file => {
      getFileInfoByUrl(file.url)
        .then((res) => {
          const { file_name, file_size, content_type } = res as { file_name: string; file_size: number; content_type: string };
          setFileList(prev => {
            const updated = prev.map(f =>
              f.uid === file.uid
                ? { ...f, status: 'done', name: file_name, size: file_size, type: transform_file_type[content_type] || content_type }
                : f
            );
            setFormFileValue(updated);
            return updated;
          });
        })
        .catch(() => {
          setFileList(prev => {
            const updated = prev.map(f => f.uid === file.uid ? { ...f, status: 'error' } : f);
            setFormFileValue(updated);
            return updated;
          });
        });
    });
  };


  const previewFileList = useMemo(() => {
    return fileList.map(file => ({
      ...file,
      url: file.thumbUrl || file.url || (file.originFileObj ? URL.createObjectURL(file.originFileObj) : undefined)
    }));
  }, [fileList]);

  const handleDelete = (file: any) => {
    const updated = fileList.filter(item =>
      item.thumbUrl && file.thumbUrl ? item.thumbUrl !== file.thumbUrl
        : item.url && file.url ? item.url !== file.url
        : item.uid !== file.uid
    );
    setFileList(updated);
    setFormFileValue(updated);
  };

  return (
    <RbModal
      title={editIndex !== undefined ? t('workflow.editChatVariable') : t('workflow.addChatVariable')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        <FormItem
          name="name"
          label={t('workflow.config.parameter-extractor.name')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('workflow.config.parameter-extractor.invalidParamName') },
          ]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="type"
          label={t('workflow.config.parameter-extractor.type')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            onChange={(value) => {
              form.setFieldValue('defaultValue', undefined);
              setFileList([]);
              if (value === 'file' || value === 'array[file]') form.setFieldsValue(defaultFileUploadValues as any);
            }}
            options={types.map(key => ({
              value: key,
              label: t(`workflow.config.parameter-extractor.${key}`),
            }))}
          />
        </FormItem>

        {type === 'file' || type === 'array[file]' ? (
          <>
            <UploadFileListModal
              ref={uploadFileListModalRef}
              featureConfig={featureConfig}
              refresh={addFileList}
            />
            <Form.Item name="defaultValue" hidden noStyle />
            <Form.Item label={t('workflow.config.parameter-extractor.default')}>
              
                <Row gutter={8}>
                  <Col span={12}>
                    <UploadFiles
                      featureConfig={featureConfig}
                      onChange={fileChange}
                      block={true}
                      textType="button"
                      disabled={type === 'file' && fileList.length > 0}
                    />
                  </Col>
                  <Col span={12}>
                    <Button block
                      disabled={type === 'file' && fileList.length > 0}
                      onClick={() => uploadFileListModalRef.current?.handleOpen()}>
                      {t('memoryConversation.addRemoteFile')}
                    </Button>
                  </Col>
                </Row>
              {previewFileList.length > 0 && (
                <Flex gap={8} wrap className="rb:mt-2!">
                  {previewFileList.map((file) => (
                    <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
                      {file.type?.includes('image') ? (
                        <div className={clsx('rb:inline-block rb:group rb:relative rb:rounded-lg rb:border', {
                          'rb:border-[#FF5D34]': file.status === 'error',
                          'rb:border-[#F6F6F6]': file.status !== 'error',
                        })}>
                          <img src={file.url} alt={file.name} className="rb:size-12! rb:rounded-lg rb:object-cover" />
                          <div
                            className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')] rb:hover:bg-[url('@/assets/images/conversation/delete_hover.svg')]"
                            onClick={() => handleDelete(file)}
                          />
                        </div>
                      ) : (
                        <Flex
                          align="center"
                          gap={10}
                          className={clsx('rb:w-45 rb:text-[12px] rb:group rb:relative rb:rounded-lg rb:bg-[#F6F6F6] rb:py-2! rb:px-2.5! rb:border', {
                            'rb:border-[#FF5D34]': file.status === 'error',
                            'rb:border-[#F6F6F6]': file.status !== 'error',
                          })}
                        >
                          <div className={clsx(
                            "rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/conversation/pdf_disabled.svg')]",
                            file.type?.includes('pdf') ? "rb:bg-[url('@/assets/images/file/pdf.svg')]" :
                              (file.type?.includes('excel') || file.type?.includes('spreadsheetml')) ? "rb:bg-[url('@/assets/images/file/excel.svg')]" :
                                file.type?.includes('csv') ? "rb:bg-[url('@/assets/images/file/csv.svg')]" :
                                  file.type?.includes('json') ? "rb:bg-[url('@/assets/images/file/json.svg')]" :
                                    file.type?.includes('ppt') ? "rb:bg-[url('@/assets/images/file/ppt.svg')]" :
                                      file.type?.includes('text') ? "rb:bg-[url('@/assets/images/file/txt.svg')]" :
                                        file.type?.includes('markdown') ? "rb:bg-[url('@/assets/images/file/md.svg')]" :
                                          (file.type?.includes('doc') || file.type?.includes('word')) ? "rb:bg-[url('@/assets/images/file/word.svg')]" : null
                          )} />
                          <div className="rb:flex-1 rb:w-32.5">
                            <div className="rb:leading-4 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.name}</div>
                            <div className="rb:leading-3.5 rb:mt-0.5 rb:text-[#5B6167] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">
                              {file.type?.split('/').pop()} · {file.size}
                            </div>
                          </div>
                          <div
                            className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
                            onClick={() => handleDelete(file)}
                          />
                        </Flex>
                      )}
                    </Spin>
                  ))}
                </Flex>
              )}
            </Form.Item>
          </>
        ) : (
          <Form.Item name="defaultValue" label={t('workflow.config.parameter-extractor.default')}>
            {type === 'number'
              ? <InputNumber placeholder={t('common.enter')} style={{ width: '100%' }} />
              : type === 'boolean'
              ? <Select
                  placeholder={t('common.pleaseSelect')}
                  options={[{ value: true, label: 'true' }, { value: false, label: 'false' }]}
                />
              : <Input placeholder={t('common.enter')} />
            }
          </Form.Item>
        )}

        <FormItem name="description" label={t('workflow.config.parameter-extractor.desc')}>
          <Input.TextArea placeholder={t('common.enter')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ChatVariableModal;
