/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-30 13:59:36 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-08 11:05:34
 */
import { forwardRef, useImperativeHandle, useState, useRef, useMemo } from 'react';
import { Form, Input, Select, InputNumber, Button, Row, Col, Flex, Spin } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
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
import RadioGroupBtn from '../Properties/RadioGroupBtn';
import CodeMirrorEditor from '@/components/CodeMirrorEditor';
import FileList from '@/components/Chat/FileList'

const FormItem = Form.Item;

const object_placeholder = `# example
# {
#   "name": "redbear",
#   "age": 2
# }`

const array_object_placeholder = `# example
# [
#   {
#     "name": "redbear",
#     "age": 2
#   },
#   {
#     "name": "redbear",
#     "age": 2
#   }
# ]
`

interface ChatVariableModalProps {
  refresh: (value: ChatVariable, editIndex?: number) => void;
  variables?: ChatVariable[];
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
  refresh,
  variables
}, ref) => {
  const { t } = useTranslation();
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null);

  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ChatVariable>();
  const [loading, setLoading] = useState(false);
  const [fileList, setFileList] = useState<any[]>([]);
  const [editIndex, setEditIndex] = useState<number | undefined>(undefined);

  const type = Form.useWatch('type', form);
  const max_size = 50;
  const allowed_transfer_methods = Form.useWatch('allowed_transfer_methods', form);
  const image_enabled = Form.useWatch('image_enabled', form);
  const audio_enabled = Form.useWatch('audio_enabled', form);
  const document_enabled = Form.useWatch('document_enabled', form);
  const video_enabled = Form.useWatch('video_enabled', form);
  const image_allowed_extensions = Form.useWatch('image_allowed_extensions', form);
  const audio_allowed_extensions = Form.useWatch('audio_allowed_extensions', form);
  const document_allowed_extensions = Form.useWatch('document_allowed_extensions', form);
  const video_allowed_extensions = Form.useWatch('video_allowed_extensions', form);
  const max_file_count = Form.useWatch('max_file_count', form);

  const featureConfig = useMemo(() => ({
    enabled: true,
    allowed_transfer_methods,
    max_file_count,
    image_enabled, image_max_size_mb: max_size, image_allowed_extensions,
    audio_enabled, audio_max_size_mb: max_size, audio_allowed_extensions,
    document_enabled, document_max_size_mb: max_size, document_allowed_extensions,
    video_enabled, video_max_size_mb: max_size, video_allowed_extensions,
  }), [
    allowed_transfer_methods, max_file_count,
    image_enabled, image_allowed_extensions,
    audio_enabled, audio_allowed_extensions,
    document_enabled, document_allowed_extensions,
    video_enabled, video_allowed_extensions, max_size
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
      } else if (variable.type.includes('object') && variable.defaultValue) {
        form.setFieldValue('defaultValue', variable.defaultValue ? JSON.stringify(variable.defaultValue, null, 2) : undefined)
      }
    } else {
      form.resetFields();
      setEditIndex(undefined);
    }
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      const defaultValue = Array.isArray(values.defaultValue)
        ? values.defaultValue.filter((v: any) => v !== undefined && v !== null && v !== '')
        : values.type.includes('object')
        ? JSON.parse(values.defaultValue)
        : values.defaultValue;
      refresh({ ...values, defaultValue }, editIndex);
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
            {
              validator: (_, value) => {
                const duplicate = variables?.some((v, i) => v.name === value && i !== editIndex);
                return duplicate ? Promise.reject(t('workflow.config.duplicateName')) : Promise.resolve();
              }
            },
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
              form.setFieldValue('defaultValue', value === 'array[string]' ? [] : undefined);
              setFileList([]);
              if (value === 'file' || value === 'array[file]') form.setFieldsValue(defaultFileUploadValues as any);
            }}
            options={types.map(key => ({
              value: key,
              label: t(`workflow.config.parameter-extractor.${key}`),
            }))}
          />
        </FormItem>

        {type?.includes('file')
        ? (
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
                <FileList wrap="wrap" fileList={previewFileList} onDelete={handleDelete} className="rb:mt-2!" />
              )}
            </Form.Item>
          </>
        )
        : ['array[string]', 'array[number]', 'array[boolean]'].includes(type)
        ? (
          <Form.Item label={t('workflow.config.parameter-extractor.default')}>
            <Form.List name="defaultValue">
              {(fields, { add, remove }) => (
                <Flex vertical gap={8}>
                  {fields.map(({ key, name }) => (
                    <Flex key={key} align="center" gap={4}>
                      <Form.Item name={name} noStyle>
                        {type === 'array[number]'
                          ? <InputNumber placeholder={t('common.enter')} className="rb:flex-1!" />
                          : type === 'array[boolean]'
                          ? <RadioGroupBtn size="large" options={[{ value: true, label: 'True' }, { value: false, label: 'False' }]} className="rb:flex-1!" />
                          : <Input placeholder={t('common.enter')} className="rb:flex-1!" />
                        }
                      </Form.Item>
                      <div
                        className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                        onClick={() => remove(name)}
                      ></div>
                    </Flex>
                  ))}
                  <Button type="dashed" onClick={() => add()} icon={<PlusOutlined />} block>
                    {t('common.add')}
                  </Button>
                </Flex>
              )}
            </Form.List>
          </Form.Item>
        )
        : (
          <Form.Item
            name="defaultValue"
            label={t('workflow.config.parameter-extractor.default')}
            rules={[
              (type === 'object' || type === 'array[object]') ? {
                validator: (_, value) => {
                  if (!value) return Promise.resolve();
                  try { JSON.parse(value); return Promise.resolve(); }
                  catch { return Promise.reject(t('workflow.invalidJSON')); }
                }
              } : {}
            ]}
          >
            {type === 'number'
              ? <InputNumber placeholder={t('common.enter')} style={{ width: '100%' }} />
              : type === 'boolean'
              ? <RadioGroupBtn size="large" options={[{ value: true, label: 'True' }, { value: false, label: 'False' }]} />
              : type === 'object' || type === 'array[object]'
              ? <CodeMirrorEditor
                language="json"
                placeholder={type === 'object' ? object_placeholder : array_object_placeholder}
                variant="outlined"
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
