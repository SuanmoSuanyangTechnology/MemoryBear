/*
 * @Author: ZhaoYing
 * @Date: 2026-06-09
 * Structured Output Schema Modal
 */
import { forwardRef, useImperativeHandle, useState, useMemo, useRef } from 'react';
import { Button, Flex, Space, Tree, Form, App } from 'antd';
import { DownOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import PageTabs from '@/components/PageTabs';
import CodeMirrorEditor from '@/components/CodeMirrorEditor';
import JsonImportModal, { type JsonImportModalRef } from './JsonImportModal';
import FieldRow from './FieldRow';
import type { JsonSchema, StructuredOutputSchemaModalRef, Field } from './types';
import { defaultJsonSchema, getAllObjectKeys } from './constant'

interface StructuredOutputSchemaModalProps {
  refresh: (schema: JsonSchema) => void;
}

export const StructuredOutputSchemaModal = forwardRef<StructuredOutputSchemaModalRef, StructuredOutputSchemaModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [activeTab, setActiveTab] = useState<'visual' | 'json'>('visual');
  const [fields, setFields] = useState<Field[]>(defaultJsonSchema);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
  const [editForm] = Form.useForm();
  const importModalRef = useRef<JsonImportModalRef>(null);

  const handleOpen = (schema: JsonSchema) => {
    setFields(schema || defaultJsonSchema);
    setVisible(true);

    setExpandedKeys(getAllObjectKeys(schema || defaultJsonSchema))
  };

  const handleClose = () => {
    setVisible(false);
    setFields([...defaultJsonSchema]);
    setEditingId(null);
    setExpandedKeys([]);
  };

  const handleSave = () => {
    handleClose();
    refresh(fields);
  };

  /** Add a field at a given index path (empty path = root) */
  const addField = (parentIndexPath: number[] = []) => {
    const newField: Field = {
      name: undefined,
      type: 'string',
      description: undefined,
      children: []
    };

    let newFields: Field[];
    let newFieldIndexPath: number[];

    // 如果 parentIndexPath 为空，直接添加到根级别
    if (parentIndexPath.length === 0) {
      newFields = [...fields, newField];
      newFieldIndexPath = [fields.length];
    } else {
      // 使用索引路径更新字段
      const updateByIndexPath = (list: Field[], indices: number[], updater: (f: Field) => Field): Field[] => {
        if (indices.length === 0) return list;
        const [first, ...rest] = indices;
        return list.map((field, i) => {
          if (i !== first) return field;
          if (rest.length === 0) {
            return updater(field);
          }
          return {
            ...field,
            children: updateByIndexPath(field.children || [], rest, updater)
          };
        });
      };

      // 获取父级字段，确定新字段的位置
      const getFieldByIndexPath = (list: Field[], indices: number[]): Field | null => {
        let current: Field | null = null;
        let currentList: Field[] = list;
        for (const index of indices) {
          current = currentList[index] || null;
          if (!current) return null;
          currentList = current.children || [];
        }
        return current;
      };

      const parentField = getFieldByIndexPath(fields, parentIndexPath);
      const parentChildrenCount = parentField?.children?.length || 0;

      newFields = updateByIndexPath(fields, parentIndexPath, (field) => ({
        ...field,
        children: [...(field.children || []), newField]
      }));

      newFieldIndexPath = [...parentIndexPath, parentChildrenCount];

      // 展开父节点
      const parentKey = parentIndexPath.join(',');
      if (!expandedKeys.includes(parentKey)) {
        setExpandedKeys([...expandedKeys, parentKey]);
      }
    }

    setFields(newFields);
    setEditingId(newFieldIndexPath.join(','));
    editForm.setFieldsValue(newField);
  };

  /** Delete a field by index path (e.g., [0, 2] means the 3rd child of the 1st root field) */
  const deleteField = (indexPath: number[]) => {
    if (indexPath.length === 0) return;

    const deleteFromList = (list: Field[], indices: number[]): Field[] => {
      if (indices.length === 1) {
        // Always allow deletion
        return list.filter((_, i) => i !== indices[0]);
      }

      const [first, ...rest] = indices;
      return list.map((field, i) => {
        if (i !== first) return field;
        const children = field.children || [];
        // Always allow deletion
        return {
          ...field,
          children: deleteFromList(children, rest)
        };
      });
    };

    const newFields = deleteFromList(fields, indexPath);
    setFields(newFields);
  };

  const toggleEdit = (id: string) => {
    console.log('toggleEdit', editingId, id )

    setEditingId(editingId === id ? null : id);
    if (editingId !== id) {
      setTimeout(() => {
        editForm.resetFields();
      }, 0);
    }
  };

  /** Exit edit mode, removing the field if it was a freshly added (unnamed) one */
  const handleCancelField = (indexPath: number[], field: Field) => {
    if (!field.name) {
      deleteField(indexPath);
      setEditingId(null);
    } else {
      setEditingId(null);
    }
    setTimeout(() => {
      editForm.resetFields();
    }, 0);
  };

  /** Save a field by validating and committing its current form values, then exit edit mode */
  const handleSaveField = (indexPath: number[]) => {
    const values = editForm.getFieldsValue() as Partial<Field>;
    const newName = (values.name || '').trim();
    // Validation: name cannot be empty
    if (!newName) {
      message.warning(t('workflow.config.llm.fieldNameRequired') || '字段名不能为空');
      return;
    }

    // Find the field using index path
    const getFieldByIndexPath = (list: Field[], indices: number[]): Field | null => {
      if (indices.length === 0) return null;
      let current: Field | null = null;
      let currentList: Field[] = list;
      for (const index of indices) {
        current = currentList[index] || null;
        if (!current) return null;
        currentList = current.children || [];
      }
      return current;
    };

    // Find parent field and siblings using index path
    const parentIndexPath = indexPath.slice(0, -1);
    const parentField = parentIndexPath.length > 0
      ? getFieldByIndexPath(fields, parentIndexPath)
      : null;
    const siblings = parentIndexPath.length === 0
      ? fields.map((f) => f.name)
      : (parentField?.children || []).map((c) => c.name);

    // Get old name from the field being edited
    const editedField = getFieldByIndexPath(fields, indexPath);
    const oldName = editedField?.name;

    // Validation: name must be unique within its parent (excluding the field itself)
    const conflict = siblings.some((n) => n === newName && n !== oldName);
    if (conflict) {
      message.warning(t('workflow.config.llm.fieldNameDuplicate') || '字段名重复');
      return;
    }

    const updatedField: Field = {
      name: newName,
      type: values.type || 'string',
      description: values.description || '',
      required: values.required,
      children: editedField?.children
    };

    // Update field by index path
    const updateByIndexPath = (list: Field[], indices: number[], updater: (f: Field) => Field): Field[] => {
      if (indices.length === 0) return list;
      const [first, ...rest] = indices;
      return list.map((field, i) => {
        if (i !== first) return field;
        if (rest.length === 0) {
          return updater(field);
        }
        return {
          ...field,
          children: updateByIndexPath(field.children || [], rest, updater)
        };
      });
    };

    const newFields = updateByIndexPath(fields, indexPath, () => updatedField);
    setFields(newFields);
    setEditingId(null);
  };

  /** Recursively convert fields to Tree data */
  const fieldsToTreeData = (fieldsList: Field[], parentPath: (string | undefined)[] = [], indexPath: number[] = []): DataNode[] => {
    return fieldsList.map((field, index) => {
      const currentIndexPath = [...indexPath, index];
      return {
        key: currentIndexPath.join(','),
        title: (
          <FieldRow
            field={field}
            indexPath={currentIndexPath}
            editingId={editingId}
            isEditing={editingId === currentIndexPath.join(',')}
            editForm={editForm}
            onAddField={addField}
            onDeleteField={deleteField}
            onToggleEdit={toggleEdit}
            onSaveField={handleSaveField}
            onCancel={handleCancelField}
          />
        ),
        children: field.type.includes('object') && field.children
          ? fieldsToTreeData(field.children, [...parentPath, field.name], currentIndexPath)
          : undefined
      };
    });
  };

  const openImportModal = () => {
    importModalRef.current?.handleOpen();
  };

  /** Called by JsonImportModal on submit, with the converted Field[] */
  const handleImportSubmit = (next: Field[]) => {
    setFields(next);
  };

  /** Derive fields and build tree data + field map in one pass */
  const treeData: DataNode[] = useMemo(() => {
    const walk = (list: Field[]) => {
      list.forEach((f) => {
        if (f.children) walk(f.children);
      });
    };
    walk(fields);
    if (editingId) return fieldsToTreeData(fields);
    return [
      ...fieldsToTreeData(fields),
      {
        key: '__add_field_root__',
        isLeaf: true
      }
    ]
  }, [fields, fieldsToTreeData]);

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <>
    <RbModal
      title={t('workflow.config.llm.structuredOutputSchema')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.confirm')}
      onOk={handleSave}
      cancelText={t('common.cancel')}
      width={1000}
    >
      <Flex align="center" justify="space-between">
        <PageTabs
          value={activeTab}
          onChange={(value) => setActiveTab(value as 'visual' | 'json')}
          options={[
            { label: t('workflow.config.llm.visualEditor'), value: 'visual' },
            { label: t('workflow.config.llm.jsonSchema'), value: 'json' }
          ]}
        />
        <Space size={4}>
          <Button
            size="small"
            type="text"
            onClick={openImportModal}
          >
            {t('workflow.config.llm.importFromJson')}
          </Button>
        </Space>
      </Flex>
      {activeTab === 'visual' && (
        <div className="rb:mt-4 rb:px-4 rb:py-3 rb:bg-[#F6F6F6] rb:rounded-lg rb:text-[12px]">
          <Flex align="center" gap={8}>
            <span className="rb:font-medium">structured_output</span>
            <span className="rb:text-[#5B6167]">object</span>
          </Flex>

          <Tree
            className="rb:bg-transparent! rb:text-[12px]!"
            treeData={treeData}
            switcherIcon={<DownOutlined />}
            showLine
            blockNode
            expandedKeys={expandedKeys}
            onExpand={(keys) => setExpandedKeys(keys)}
            selectable={false}
            titleRender={(node) => {
              if (node.key === '__add_field_root__') {
                return (
                  <Button
                    size="small"
                    type="default"
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      addField();
                    }}
                  >
                    + {t('workflow.config.llm.addField')}
                  </Button>
                );
              }
              return node.title as React.ReactNode;
            }}
          />
        </div>
      )}
      {activeTab === 'json' && (
        <div className="rb:mt-4">
          <CodeMirrorEditor
            value={JSON.stringify(fields, null, 2)}
            language="json"
            variant="outlined"
            height="320px"
            placeholder={t('workflow.config.llm.jsonSchemaPlaceholder')}
          />
        </div>
      )}
    </RbModal>
    <JsonImportModal
      ref={importModalRef}
      onSubmit={handleImportSubmit}
    />
    </>
  );
});

export default StructuredOutputSchemaModal;
