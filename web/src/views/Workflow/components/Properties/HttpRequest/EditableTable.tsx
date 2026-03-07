import { type FC, useMemo } from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Select, Table, Form, type TableProps, Flex } from 'antd';

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';
import Empty from '@/components/Empty';
import Editor from '../../Editor'

export interface TableRow {
  key?: string;
  name?: string;
  value?: string;
  type?: string;
}

interface EditableTableProps {
  parentName: string | string[];
  title?: string;
  options?: Suggestion[];
  typeOptions?: { value: string, label: string }[]
  filterBooleanType?: boolean;
  size?: "small"
}

const EditableTable: FC<EditableTableProps> = ({
  parentName,
  title,
  options = [],
  typeOptions = [],
  filterBooleanType = false,
  size = 'small'
}) => {
  const { t } = useTranslation();

  const createNewRow = (): TableRow => ({
    name: undefined,
    value: undefined,
    ...(typeOptions.length > 0 && { type: typeOptions[0].value })
  });

  // Filter options based on boolean type if needed
  const booleanFilterOptions = useMemo(() => {
    return filterBooleanType
      ? options.filter(option => option.dataType !== 'boolean')
      : options
  }, [options, filterBooleanType])

  const getColumns = (remove: (index: number) => void): TableProps<TableRow>['columns'] => {
    const hasType = typeOptions.length > 0;
    const contentClassName = hasType ? 'rb:w-[120px]!' : "rb:w-[154px]!"
    const formClassName = 'rb:mb-0! rb:bg-[#F6F6F6] rb:rounded-[8px] rb:py-[2px]! rb:px-[6px]!'

    return [
      {
        title: t('workflow.config.name'),
        dataIndex: 'name',
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item name={[index, 'name']} className={formClassName}>
            <Editor
              options={booleanFilterOptions.filter(option => !option.dataType.includes('file'))}
              type="input"
              className={contentClassName}
              size={size}
              variant="borderless"
            />
          </Form.Item>
        )
      },
      ...(hasType ? [{
        title: t('workflow.config.type'),
        dataIndex: 'type',
        width: '20%',
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item shouldUpdate noStyle>
            {(form) => (
              <Form.Item name={[index, 'type']} noStyle>
                <Select 
                  placeholder={t('common.pleaseSelect')} 
                  // size="small" 
                  options={typeOptions}
                  popupMatchSelectWidth={false}
                  onChange={() => {
                    form.setFieldValue([...Array.isArray(parentName) ? parentName : [parentName], index, 'value'], undefined);
                  }}
                  size={size}
                  variant="borderless"
                  className="rb:w-17! select"
                />
              </Form.Item>
            )}
          </Form.Item>
        )
      }] : []),
      {
        title: t('workflow.config.value'),
        dataIndex: 'value',
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item 
            shouldUpdate={(prevValues, currentValues) => {
              const prevType = prevValues?.[Array.isArray(parentName) ? parentName.join('.') : parentName]?.[index]?.type;
              const currentType = currentValues?.[Array.isArray(parentName) ? parentName.join('.') : parentName]?.[index]?.type;
              return prevType !== currentType;
            }}
            noStyle
          >
            {(form) => {
              const currentType = form.getFieldValue([...Array.isArray(parentName) ? parentName : [parentName], index, 'type']);
              const filteredOptions = currentType === 'file'
                ? booleanFilterOptions.filter(option => option.dataType.includes('file'))
                : booleanFilterOptions.filter(option => !option.dataType.includes('file'));
              
              return (
                <Form.Item name={[index, 'value']} className={formClassName}>
                  <Editor
                    options={filteredOptions}
                    type="input"
                    className={contentClassName}
                    size={size}
                    variant="borderless"
                  />
                </Form.Item>
              );
            }}
          </Form.Item>
        )
      },
      {
        title: '',
        dataIndex: 'actions',
        render: (_: any, __: TableRow, index: number) => (
          <div
            className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
            onClick={() => remove(index)}
          ></div>
        )
      }
    ];
  };

  return (
    <div className="rb:mb-4">
      <Form.List name={parentName}>
        {(fields, { add, remove }) => {
          const AddButton = ({ block = false }: { block?: boolean }) => (
            <Button
              onClick={() => add(createNewRow())} 
              size="small"
              block={block}
              className={block ? "rb:mt-2 rb:text-[12px]! rb:bg-transparent! rb:rounded-md" : "rb:text-[12px]! rb:rounded-sm!"}
            >
              + {t('common.add')}
            </Button>
          );

          return (
            <>
              {title && (
                <Flex align="center" justify="space-between" className="rb:mb-2!">
                  <div className="rb:font-medium rb:text-[12px] rb:leading-4.5">{title}</div>
                  <AddButton block={false} />
                </Flex>
              )}
              
              <Table<TableRow>
                bordered={false}
                dataSource={fields.map((field) => ({ 
                  key: String(field.key),
                  name: undefined,
                  value: undefined,
                  type: undefined
                }))}
                columns={getColumns(remove)}
                pagination={false}
                size="small"
                locale={{ emptyText: <Empty size={88} /> }}
              />
              
              {!title && <AddButton block />}
            </>
          );
        }}
      </Form.List>
    </div>
  );
};

export default EditableTable;