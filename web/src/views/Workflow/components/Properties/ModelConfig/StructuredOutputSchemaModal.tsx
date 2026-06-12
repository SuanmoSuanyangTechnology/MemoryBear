/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-09 
 * Structured Output Schema Modal
 */

import { forwardRef, useImperativeHandle, useState, useMemo, useRef } from 'react';
import { Button, Input, Select, Tooltip, Flex, Space, Divider, Tree, Form, App, Switch } from 'antd';
import { DownOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import PageTabs from '@/components/PageTabs';
import CodeMirrorEditor from '@/components/CodeMirrorEditor';
import JsonImportModal, { type JsonImportModalRef } from './JsonImportModal';
import clsx from 'clsx';

export interface StructuredOutputSchemaModalRef {
  handleOpen: (schema: JsonSchema) => void;
  handleClose: () => void;
}

export interface Field {
  name?: string;
  type: string;
  children?: Field[];
  description?: string;
  required?: boolean;
}

export type JsonSchema = Field[];

const defaultJsonSchema: JsonSchema = []

const typeOptions = [
  { value: 'string', label: 'string' },
  { value: 'number', label: 'number' },
  { value: 'boolean', label: 'boolean' },
  { value: 'object', label: 'object' },
  { value: 'array[string]', label: 'array[string]' },
  { value: 'array[number]', label: 'array[number]' },
  { value: 'array[object]', label: 'array[object]' },
];

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
  const [editForm] = Form.useForm();
  const importModalRef = useRef<JsonImportModalRef>(null);

  const handleOpen = (schema: JsonSchema) => {
    setFields(schema || defaultJsonSchema);
    setVisible(true);
  };

  const handleClose = () => {
    setVisible(false);
    setFields([...defaultJsonSchema]);
    setEditingId(null);
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
        // Check if it's the last field and whether it's a new field (name is undefined)
        const isNewField = !list[indices[0]]?.name;
        if (isNewField || list.length > 1) {
          return list.filter((_, i) => i !== indices[0]);
        }
        return list;
      }
      
      const [first, ...rest] = indices;
      return list.map((field, i) => {
        if (i !== first) return field;
        const children = field.children || [];
        // Check if it's the last child and whether it's a new field
        const isNewField = rest.length === 1 && !children[rest[0]]?.name;
        if (isNewField || children.length > 1) {
          return {
            ...field,
            children: deleteFromList(children, rest)
          };
        }
        return field;
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

  /** Render a single field row (used recursively for nested fields) */
  const renderFieldRow = (field: Field, indexPath: number[] = []): React.ReactNode => {
    const currentIndexPath = indexPath.join(',');
    const isEditing = editingId === currentIndexPath;
    const isObject = field.type.includes('object');

    return (
      <div
        onClick={(e) => e.stopPropagation()}
        className={clsx(
          'rb:flex rb:items-center rb:gap-4 rb:py-1 rb:px-3 rb:rounded-lg',
          {
            'rb-border rb:bg-white': isEditing
          }
        )}
      >
        {isEditing ? (
          <Form
            form={editForm}
            layout="inline"
            className="rb:flex-1"
            initialValues={{
              name: !field.name ? undefined : field.name,
              type: field.type,
              description: field.description,
              required: field.required
            }}
          >
            <Flex justify="space-between" className="rb:w-full!">
              <Space>
                <Form.Item name="name" noStyle>
                  <Input
                    size="small"
                    placeholder={t('workflow.config.llm.fieldName')}
                    variant="borderless"
                  />
                </Form.Item>
                <Form.Item name="type" noStyle>
                  <Select
                    size="small"
                    options={typeOptions}
                    className="rb:w-34!"
                    variant="borderless"
                  />
                </Form.Item>
              </Space>

              <Flex align="center" gap={8}>
                <Form.Item name="required" noStyle valuePropName="checked">
                  <Switch
                    size="small"
                    checkedChildren={t('workflow.config.llm.required')}
                    unCheckedChildren={t('workflow.config.llm.unRequired')}
                    onClick={(_, e) => e.stopPropagation()}
                  />
                </Form.Item>

                <Button
                  size="small"
                  className="rb:text-[12px]!"
                  onClick={(e) => {
                    e.stopPropagation();
                    // If the field is a newly added field (name is undefined), remove it from
                    // the schema on cancel to keep the data clean.
                    if (!field.name) {
                      deleteField(indexPath);
                      setEditingId(null);
                    } else {
                      setEditingId(null);
                    }

                    setTimeout(() => {
                      editForm.resetFields();
                    }, 0);
                  }}
                >{t('common.cancel')}</Button>

                <Button
                  size="small"
                  type="primary"
                  className="rb:text-[12px]!"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSaveField(indexPath);
                  }}
                >
                  {t('common.confirm')}
                </Button>
              </Flex>
            </Flex>

            <Form.Item name="description" className="rb:flex-1 rb:mb-0!">
              <Input
                size="small"
                variant="borderless"
                placeholder={t('workflow.config.llm.addDescription')}
              />
            </Form.Item>
          </Form>
        ) : (
          <Flex justify="space-between" gap={8} className="rb:w-full!">
            <div>
              <Flex align="center" gap={8} className="rb:shrink-0">
                <span className="rb:text-sm rb:font-medium">{field.name || t('workflow.config.llm.fieldName')}</span>
                <span className="rb:text-xs rb:text-[#5B6167]">{field.type}</span>
                {field.required && (
                  <span className="rb:text-xs rb:text-[#F5222D]">{t('workflow.config.llm.required')}</span>
                )}
              </Flex>

              {field.description &&
                <div className="rb:flex-1 rb:text-[#5B6167]">
                  {field.description}
                </div>
              }
            </div>

            <div className="rb:flex rb:items-center rb:gap-2">
              {isObject && (
                <Tooltip title={t('workflow.config.llm.addField')}>
                  <div
                    className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/plus_light_grey.svg')]"
                    onClick={(e) => {
                      e.stopPropagation();
                      addField(indexPath);
                    }}
                  />
                </Tooltip>
              )}

              <Tooltip title={t('common.edit')}>
                <div
                  className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/edit.svg')]"
                  onClick={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    toggleEdit(currentIndexPath);
                  }}
                ></div>
              </Tooltip>

              <Tooltip title={t('common.delete')}>
                <div
                  className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete_red.svg')]"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteField(indexPath);
                  }}
                ></div>
              </Tooltip>
            </div>
          </Flex>
        )}
      </div>
    );
  };

  /** Recursively convert fields to Tree data */
  const fieldsToTreeData = (fieldsList: Field[], parentPath: (string | undefined)[] = [], indexPath: number[] = []): DataNode[] => {
    return fieldsList.map((field, index) => {
      const currentIndexPath = [...indexPath, index];
      return {
        key: currentIndexPath.join(','),
        title: renderFieldRow(field, currentIndexPath),
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

  const syncToJson = () => {
    // TODO: Implement sync to JSON logic
  };

  /** Derive fields and build tree data + field map in one pass */
  const treeData: DataNode[] = useMemo(() => {
    const walk = (list: Field[]) => {
      list.forEach((f) => {
        if (f.children) walk(f.children);
      });
    };
    walk(fields);
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

  console.log('treeData', treeData)
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
            onClick={syncToJson}
          >
            AI 生成
          </Button>
          <Divider type="vertical" />
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
            defaultExpandAll
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
            // onChange={(value) => {
            //   try {
            //     setFields(JSON.parse(value));
            //   } catch (error) {
            //     console.error('Invalid JSON', error);
            //   }
            // }}
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