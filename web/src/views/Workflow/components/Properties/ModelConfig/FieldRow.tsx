/*
 * @Author: ZhaoYing 
 * @Date: 2026-07-13 17:51:26 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-15 10:32:09
 */
/*
 * Field Row Component
 * Renders a single field row in the structured output schema tree,
 * including the inline edit form and the display layout.
 */
import { Button, Input, Select, Tooltip, Flex, Space, Form, Switch } from 'antd';
import type { FormInstance } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { Field } from './types';
import { typeOptions } from './constant';

interface FieldRowProps {
  field: Field;
  indexPath: number[];
  editingId: string | null;
  isEditing: boolean;
  editForm: FormInstance;
  onAddField: (indexPath: number[]) => void;
  onDeleteField: (indexPath: number[]) => void;
  onToggleEdit: (id: string) => void;
  onSaveField: (indexPath: number[]) => void;
  onCancel: (indexPath: number[], field: Field) => void;
}

const FieldRow = ({
  field,
  indexPath,
  editingId,
  isEditing,
  editForm,
  onAddField,
  onDeleteField,
  onToggleEdit,
  onSaveField,
  onCancel,
}: FieldRowProps) => {
  const { t } = useTranslation();
  const currentIndexPath = indexPath.join(',');
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
                  onCancel(indexPath, field);
                }}
              >{t('common.cancel')}</Button>

              <Button
                size="small"
                type="primary"
                className="rb:text-[12px]!"
                onClick={(e) => {
                  e.stopPropagation();
                  onSaveField(indexPath);
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

          {!editingId &&
            <div className="rb:flex rb:items-center rb:gap-2">
              {isObject && (
                <Tooltip title={t('workflow.config.llm.addField')}>
                  <div
                    className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/plus_light_grey.svg')]"
                    onClick={(e) => {
                      e.stopPropagation();
                      onAddField(indexPath);
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
                    onToggleEdit(currentIndexPath);
                  }}
                ></div>
              </Tooltip>

              <Tooltip title={t('common.delete')}>
                <div
                  className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete_red.svg')]"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteField(indexPath);
                  }}
                ></div>
              </Tooltip>
            </div>
          }
        </Flex>
      )}
    </div>
  );
};

export default FieldRow;
