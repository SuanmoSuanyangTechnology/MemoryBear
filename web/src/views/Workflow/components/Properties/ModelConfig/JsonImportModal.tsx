/*
 * @Author: ZhaoYing
 * @Date: 2026-06-12
 * JSON Import Modal
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { App, Button, Flex, Form, Tooltip } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import CodeMirrorEditor from '@/components/CodeMirrorEditor';
import type { Field } from './StructuredOutputSchemaModal';

export interface JsonImportModalRef {
  handleOpen: (text?: string) => void;
  handleClose: () => void;
}

interface JsonImportModalProps {
  onSubmit: (fields: Field[]) => void;
}

const JsonImportModal = forwardRef<JsonImportModalRef, JsonImportModalProps>(({
  onSubmit,
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ json: string }>();

  const handleOpen = () => {
    setVisible(true);
  };

  const handleClose = () => {
    setVisible(false);
  };

  /** Normalize a JSON Schema node type (or plain JS value) into one of the supported Field.type values */
  const resolveType = (node: any): string => {
    if (node === null || node === undefined) return 'string';
    if (typeof node === 'string') return 'string';
    if (typeof node === 'number') return 'number';
    if (typeof node === 'boolean') return 'boolean';
    if (Array.isArray(node)) {
      if (node.length === 0) return 'array[string]';
      const first = node[0];
      if (first && typeof first === 'object' && !Array.isArray(first)) return 'array[object]';
      if (typeof first === 'number') return 'array[number]';
      return 'array[string]';
    }
    if (typeof node !== 'object') return 'string';

    // Plain object without an explicit JSON Schema `type` → treat as generic object
    if (node.type === undefined) return 'object';

    if (node.type === 'array') {
      const itemType = node.items?.type || 'string';
      if (itemType === 'object') return 'array[object]';
      if (itemType === 'number' || itemType === 'integer') return 'array[number]';
      return 'array[string]';
    }
    if (node.type === 'integer') return 'number';
    if (['string', 'number', 'boolean', 'object'].includes(node.type)) return node.type;
    return 'string';
  };

  /** Convert a JSON Schema object (or plain JSON object / array of fields) into the internal Field[] format */
  const convertJsonToFields = (raw: any): Field[] => {
    if (!raw) return [];
    // Direct array of fields - already in internal format
    if (Array.isArray(raw)) {
      return raw
        .filter((f) => f && typeof f === 'object')
        .map((f) => ({
          name: f.name,
          type: f.type || resolveType(f),
          description: f.description,
          required: f.required,
          children: f.children ? convertJsonToFields(f.children) : undefined,
        }));
    }
    if (typeof raw !== 'object') return [];

    // Standard JSON Schema: { type: 'object', properties: { ... }, required: [...] }
    // Fall back to plain JSON object: { aaa: 'bbb', ccc: { ... } } where each key is a field name
    const isJsonSchema = raw.type !== undefined || raw.properties !== undefined;
    const source: Record<string, any> = isJsonSchema && raw.properties && typeof raw.properties === 'object'
      ? raw.properties
      : raw;
    const requiredKeys: string[] = Array.isArray(raw.required) ? raw.required : [];

    return Object.entries(source).map(([key, value]: [string, any]) => {
      const field: Field = {
        name: key,
        type: resolveType(value),
        required: requiredKeys.includes(key) || undefined,
      };
      if (value && typeof value === 'object' && value.description) {
        field.description = String(value.description);
      }
      if (Array.isArray(value) && value.length > 0 && value[0] && typeof value[0] === 'object') {
        // Plain array of objects → use the first element as a template child
        field.children = convertJsonToFields(value[0]);
      } else if (value && typeof value === 'object' && !Array.isArray(value)) {
        if (value.type === 'object' && value.properties) {
          field.children = convertJsonToFields(value);
        } else if (value.type === 'array' && value.items?.type === 'object' && value.items.properties) {
          field.children = convertJsonToFields(value.items);
        } else if (Array.isArray(value.children)) {
          field.children = convertJsonToFields(value.children);
        } else if (!value.type) {
          // Plain nested object → treat nested keys as children
          field.children = convertJsonToFields(value);
        }
      }
      return field;
    });
  };

  const handleOk = () => {
    const trimmed = (form.getFieldValue('json') || '').trim();
    if (!trimmed) {
      message.error(t('workflow.invalidJSON'));
      return;
    }
    let parsed: any;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      message.error(t('workflow.invalidJSON'));
      return;
    }
    const next = convertJsonToFields(parsed);
    if (!next.length) {
      message.error(t('workflow.invalidJSON'));
      return;
    }
    onSubmit(next);
    handleClose();
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
  }));

  return (
    <RbModal
      title={t('workflow.config.llm.importFromJson')}
      open={visible}
      onCancel={handleClose}
      onOk={handleOk}
      okText={t('common.confirm')}
      cancelText={t('common.cancel')}
    >
      <Flex align="center" justify="space-between" className="rb:mb-1">
        <span className="rb:text-[12px] rb:text-[#5B6167]">JSON</span>
        <Tooltip title={t('common.copy')}>
          <Button
            type="text"
            size="small"
            icon={<CopyOutlined />}
            onClick={() => {
              const json = form.getFieldValue('json');
              if (json) {
                navigator.clipboard?.writeText(json);
                message.success(t('common.copySuccess'));
              }
            }}
          />
        </Tooltip>
      </Flex>
      <Form form={form} component={false}>
        <Form.Item
          name="json"
          noStyle
          getValueFromEvent={(value) => value}
        >
          <CodeMirrorEditor
            language="json"
            variant="outlined"
            placeholder={t('workflow.config.llm.jsonSchemaPlaceholder')}
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default JsonImportModal;
