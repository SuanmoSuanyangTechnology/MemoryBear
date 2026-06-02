import { forwardRef, useImperativeHandle, useState, useRef, useMemo } from 'react';
import { Form, Input, Select, InputNumber, Checkbox, Tag, Flex, Button, Row, Col, message } from 'antd';
import { useTranslation } from 'react-i18next';

import type { Variable, VariableEditModalRef } from './types'
import RbModal from '@/components/RbModal'
import UploadFiles from '@/views/Conversation/components/FileUpload'
import type { UploadFileListModalRef } from '@/views/Conversation/types'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import { getFileInfoByUrl } from '@/api/fileStorage'
import { transform_file_type } from '@/views/Conversation/components/FileUpload'
import CodeMirrorEditor from '@/components/CodeMirrorEditor';
import FileList from '@/components/Chat/FileList'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';

const FormItem = Form.Item;

interface VariableEditModalProps {
  refresh: (values: Variable) => void;
  variables?: Variable[];
}

const variableType: Record<string, string> = {
  'text-input': 'string',
  'paragraph': 'string',
  'select': 'string',
  'number': 'number',
  'checkbox': 'boolean',
  'file-upload': 'file',
  'file-list-upload': 'array[file]',
  'json-editor': 'object',
}
const object_placeholder = `{
  "type": "object",
  "properties": {
    "foo": {
      "type": "string"
    },
    "bar": {
      "type": "object",
      "properties": {
        "sub": {
          "type": "number"
        }
      },
      "required": [],
      "additionalProperties": true
    }
  },
  "required": [],
  "additionalProperties": true
}`
const initialValues = {
  max_length: 48,
  required: true
}

const VariableEditModal = forwardRef<VariableEditModalRef, VariableEditModalProps>(({
  refresh,
  variables
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<Variable>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<Variable | null>(null)

  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null);
  const [fileList, setFileList] = useState<any[]>([]);
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

  const featureConfig: FeaturesConfigForm['file_upload'] = useMemo(() => ({
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

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
    setFileList([])
  };

  const handleOpen = (variable?: Variable) => {
    setVisible(true);
    if (variable) {
      setEditVo({
        ...variable,
        ui_type: variable.ui_type
          || (variable.type === 'string'
            ? 'text-input'
            : variable.type === 'number'
            ? 'number'
            : 'boolean'),
      })
      form.setFieldsValue(variable)
      if (variable.type === 'file' || variable.type === 'array[file]') {
        setFileList(Array.isArray(variable.default)
          ? variable.default
          : variable.default
          ? [variable.default]
          : []
        )
      }
    } else {
      form.resetFields();
    }
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form.validateFields().then((values) => {
      if (values.ui_type === 'select' && (!values.options || values.options?.length < 1)) {
        message.warning(t('workflow.config.start.selectRequired'))
        return
      }
      refresh({
        ...(editVo || {}),
        ...values,
      })
      handleClose()
    })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  const nameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (values.description && values.description !== '') return
    const { value } = e.target
    form.setFieldsValue({
      description: value,
    })
  }

  const setFormFileValue = (updated: any[]) => {
    const isSingle = form.getFieldValue('type') === 'file';
    form.setFieldValue('default', isSingle ? (updated[0] ?? null) : updated);
  };

  const fileChange = (file?: any) => {
    const fileObj = file ? {
      ...file,
      status: file.status || 'uploading',
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
      title={editVo ? t('workflow.config.start.editVariable') : t('workflow.config.addVariable')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={initialValues}
        size="middle"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        <FormItem
          name="ui_type"
          label={t('workflow.config.start.variableType')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Select
            placeholder={t('common.pleaseSelect')}
            options={Object.keys(variableType).map((key: keyof typeof variableType) => ({
              value: key,
              label: t(`workflow.config.start.${key}`),
              type: variableType[key],
            }))}
            onChange={(_value, option) => {
              form.setFieldsValue({
                default: undefined,
                options: undefined,
                type: (option as { type: string })?.type || undefined,
              })
            }}
            labelRender={(props) => <Flex align="center" justify="space-between">{props.label} <Tag color="blue">{variableType[props.value as keyof typeof variableType]}</Tag></Flex>}
            optionRender={(props) => <Flex align="center" justify="space-between">{props.label} <Tag color="blue">{variableType[props.value as keyof typeof variableType]}</Tag></Flex>}
          />
        </FormItem>
        <FormItem name="type" hidden />
        <FormItem
          name="name"
          label={t('workflow.config.start.variableName')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: t('workflow.config.start.invalidVariableName') },
            {
              validator: (_, value) => {
                const duplicate = variables?.some(v => v.name === value && v.name !== editVo?.name);
                return duplicate ? Promise.reject(t('workflow.config.duplicateName')) : Promise.resolve();
              }
            },
          ]}
        >
          <Input placeholder={t('common.enter')} onBlur={nameChange} />
        </FormItem>
        <FormItem
          name="description"
          label={t('workflow.config.start.description')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        {['text-input', 'paragraph'].includes(values?.ui_type) && (
          <FormItem
            name="max_length"
            label={t('workflow.config.start.max_length')}
          >
            <InputNumber
              placeholder={t('common.enter')}
              style={{ width: '100%' }}
              onChange={(value) => form.setFieldValue('max_length', value)}
            />
          </FormItem>
        )}
        {['select'].includes(values?.ui_type) && (
          <FormItem label={t('workflow.config.start.options')} >
            <Form.List name="options">
              {(fields, { add, remove }) => (
                <Flex vertical gap={8}>
                  {fields.map(({ key, name }) => (
                    <Flex key={key} align="center" gap={4}>
                      <Form.Item name={name} noStyle>
                        <Input placeholder={t('common.enter')} className="rb:flex-1!" />
                      </Form.Item>
                      <div
                        className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                        onClick={() => remove(name)}
                      ></div>
                    </Flex>
                  ))}
                  <Button type="dashed" onClick={() => add()} block>
                    + {t('common.add')}
                  </Button>
                </Flex>
              )}
            </Form.List>
          </FormItem>
        )}
        {values?.ui_type && !values.ui_type?.includes('file') && !(['select'].includes(values?.ui_type) && (!values?.options || values?.options?.length < 1)) && (
          <FormItem
            name="default"
            label={t('workflow.config.start.default')}
            dependencies={['text-input', 'paragraph'].includes(values.ui_type) ? ['max_length'] : []}
            rules={
              ['text-input', 'paragraph'].includes(values.ui_type) && values?.max_length
                ? [{
                    validator: (_, value) => {
                      if (!value) return Promise.resolve();
                      if (value.length > (values.max_length ?? 0)) {
                        return Promise.reject(t('workflow.config.start.maxLengthExceeded', { max: values.max_length }));
                      }
                      return Promise.resolve();
                    }
                  }]
                : ['json-editor'].includes(values.ui_type)
              ? [{
                validator: (_, value) => {
                  if (!value) return Promise.resolve();
                  try { JSON.parse(value); return Promise.resolve(); }
                  catch { return Promise.reject(t('workflow.invalidJSON')); }
                }
              }]
              : undefined
            }
          >
            {['text-input'].includes(values.ui_type)
              ? <Input placeholder={t('common.enter')} />
              : ['paragraph'].includes(values.ui_type)
              ? <Input.TextArea placeholder={t('common.enter')} />
              : ['number'].includes(values.ui_type)
              ? <InputNumber
                  placeholder={t('common.enter')}
                  style={{ width: '100%' }}
                  onChange={(value) => form.setFieldValue('default', value)}
                />
              : ['checkbox'].includes(values.ui_type)
              ? <Select
                placeholder={t('common.pleaseSelect')}
                options={[
                  { value: true, label: t('workflow.config.start.defaultChecked') },
                  { value: false, label: t('workflow.config.start.notDefaultChecked')}
                ]}
              />
              : ['select'].includes(values.ui_type) && values?.options && values?.options?.length > 0
              ? <Select
                placeholder={t('common.pleaseSelect')}
                options={[
                  {value: null, label: t('workflow.config.start.defaultEmpty')},
                  ...values?.options?.filter(Boolean).map((item) => ({ value: item, label: item }))
                ]}
              />
              : ['json-editor'].includes(values.ui_type)
              ? <CodeMirrorEditor
                language="json"
                placeholder={object_placeholder}
                variant="outlined"
              />
              : null
            }
          </FormItem>
        )}
        {values?.ui_type && values.ui_type?.includes('file') &&
         <>
          <UploadFileListModal
            ref={uploadFileListModalRef}
            refresh={addFileList}
          />
          <Form.Item name="default" hidden noStyle />
          <Form.Item label={t('workflow.config.parameter-extractor.default')}>
            <Row gutter={8}>
              <Col span={12}>
                <UploadFiles
                  featureConfig={featureConfig}
                  onChange={fileChange}
                  block={true}
                  textType="button"
                  disabled={values.ui_type === 'file-upload' && fileList.length > 0}
                />
              </Col>
              <Col span={12}>
                <Button block
                  disabled={values.ui_type === 'file-upload' && fileList.length > 0}
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
      }

        <FormItem
          name="required"
          valuePropName="checked"
        >
          <Checkbox>{t('workflow.config.start.required')}</Checkbox>
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default VariableEditModal;