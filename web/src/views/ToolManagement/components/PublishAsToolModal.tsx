/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-28 13:41:42 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-11 20:08:38
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, App } from 'antd';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';

import RbModal from '@/components/RbModal';
import type { WorkflowToolItem, InputParameters, OutputSchema, WorkflowToolModalRef } from '../types';
import { previewWorkflowToolParams, workflowPublishAsTool } from '@/api/application';
import Table from '@/components/Table';

export interface WorkflowToolForm {
  name: string;
  description?: string;
  tags: string[];
  icon?: string;
  timeout: number;
  input_parameters: InputParameters[];
  output_schema: OutputSchema;
}

const PublishAsToolModal = forwardRef<WorkflowToolModalRef, { refresh?: () => void }>(({ refresh }, ref) => {
  const { t } = useTranslation();
  const { id } = useParams();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<WorkflowToolForm>();
  const [editTool, setEditTool] = useState<WorkflowToolItem | null>(null);
  const [inputParameters, setInputParameters] = useState<InputParameters[]>([]);
  const [outputSchema, setOutputSchema] = useState<OutputSchema['properties']>({});

  const handleClose = () => {
    form.resetFields();
    setLoading(false);
    setVisible(false);
    setEditTool(null);
    setInputParameters([]);
    setOutputSchema({});
  };

  const getPreviewToolParams = () => {
    if (!id) {
      return;
    }
    previewWorkflowToolParams(id)
      .then((res) => {
        const response = res as {
          input_parameters: InputParameters[];
          output_schema: OutputSchema;
        }
        setInputParameters(response.input_parameters);
        setOutputSchema(response.output_schema.properties);
      })
  }

  const handleOpen = (tool?: WorkflowToolItem) => {
    if (tool?.id) {
      setEditTool(tool);
      form.setFieldsValue({
        name: tool.name,
        description: tool.description || '',
        tags: tool.tags || [],
      });
      setInputParameters(tool.config_data.input_parameters);
      setOutputSchema(tool.config_data.output_schema.properties);
    } else {
      form.setFieldsValue({
        name: tool?.name,
      });
      setEditTool(null);
      getPreviewToolParams();
    }
    setVisible(true);
  };

  const handleSave = () => {
    if (!id && !editTool?.id) return;

    form.validateFields()
      .then((values) => {
        setLoading(true);
        workflowPublishAsTool(editTool?.id ? editTool?.config_data.app_id : id as string, values)
          .then(() => {
            handleClose();
            message.success(editTool?.id ? t('common.updateSuccess') : t('common.createSuccess'));
            refresh?.();
          }).finally(() => {
            setLoading(false);
          })
      })
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  const inputColumns = [
    {
      title: t('tool.name'),
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: InputParameters) => (
        <div>
          <span className="rb:font-medium">{name}</span>
          <div className="rb:text-[#5B6167] rb:text-[12px]">{record.type}</div>
        </div>
      ),
    },
    {
      title: t('tool.desc'),
      dataIndex: 'description',
      key: 'description',
    },
  ]

  const outputColumns = [
    {
      title: t('tool.name'),
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: t('tool.typeDesc'),
      dataIndex: 'type',
      key: 'type',
    },
  ];

  return (
    <RbModal
      title={editTool?.id ? t('tool.editWorkflowTool') : t('tool.publishAsTool')}
      open={visible}
      onCancel={handleClose}
      confirmLoading={loading}
      okText={t('common.save')}
      onOk={handleSave}
      width={640}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          tags: [],
        }}
      >
        {/* Name */}
        <Form.Item
          name="name"
          label={t('tool.name')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { pattern: /^[a-zA-Z0-9][a-zA-Z0-9\-_]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$/, message: t('tool.requestHeaderKeyInvalid') },
          ]}
        >
          <Input placeholder={t('common.pleaseEnter')} />
        </Form.Item>

        {/* Description */}
        <Form.Item
          name="description"
          label={t('tool.toolDescription')}
        >
          <Input.TextArea
            rows={3}
            placeholder={t('common.pleaseEnter')}
          />
        </Form.Item>

        <div className="rb:mb-4">
          <div className="rb:text-[#212332] rb:text-sm rb:font-medium rb:mb-2">
            {t('tool.inputParams')}
          </div>
          <Table<InputParameters>
            initialData={inputParameters}
            columns={inputColumns}
            rowKey="name"
            pagination={false}
          />
        </div>

        {/* Output Parameters */}
        <div className="rb:mb-4">
          <div className="rb:text-[#212332] rb:text-sm rb:font-medium rb:mb-2">
            {t('tool.outputParams')}
          </div>
          <Table<{ name: string; type: string }>
            initialData={Object.keys(outputSchema).map(key => ({ name: key, type: outputSchema[key].type }))}
            columns={outputColumns}
            rowKey="name"
            pagination={false}
          />
        </div>

        {/* Tags */}
        <Form.Item
          name="tags"
          label={t('tool.tags')}
        >
          <Select
            mode="tags"
            placeholder={t('common.pleaseEnter')}
            className="rb:w-full"
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default PublishAsToolModal;
