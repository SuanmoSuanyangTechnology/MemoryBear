import { useMemo } from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Select, Table, Form, type TableProps } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';
import Empty from '@/components/Empty';
import VariableSelect from '../VariableSelect';

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
}

const EditableTable: React.FC<EditableTableProps> = ({
  parentName,
  title,
  options = [],
  typeOptions = [],
  filterBooleanType = false
}) => {
  const { t } = useTranslation();

  const createNewRow = (): TableRow => ({
    name: undefined,
    value: undefined,
    ...(typeOptions.length > 0 && { type: typeOptions[0].value })
  });

  const getColumns = (remove: (index: number) => void): TableProps<TableRow>['columns'] => {
    const hasType = typeOptions.length > 0;
    const baseWidth = hasType ? '35%' : '45%';

    return [
      {
        title: t('workflow.config.name'),
        dataIndex: 'name',
        width: baseWidth,
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item name={[index, 'name']} noStyle>
            <VariableSelect 
              placeholder={t('common.pleaseSelect')} 
              size="small" 
              options={options}
              filterBooleanType={filterBooleanType}
              popupMatchSelectWidth={false}
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
                  size="small" 
                  options={typeOptions}
                  popupMatchSelectWidth={false}
                  onChange={() => {
                    form.setFieldValue([...Array.isArray(parentName) ? parentName : [parentName], index, 'value'], undefined);
                  }}
                />
              </Form.Item>
            )}
          </Form.Item>
        )
      }] : []),
      {
        title: t('workflow.config.value'),
        dataIndex: 'value',
        width: baseWidth,
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
                ? options.filter(option => option.dataType === 'file')
                : options;
              
              return (
                <Form.Item name={[index, 'value']} noStyle>
                  <VariableSelect 
                    placeholder={t('common.pleaseSelect')} 
                    size="small" 
                    options={filteredOptions}
                    filterBooleanType={filterBooleanType}
                    popupMatchSelectWidth={false}
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
        width: '10%',
        render: (_: any, __: TableRow, index: number) => (
          <Button type="text" icon={<DeleteOutlined />} onClick={() => remove(index)} />
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
              type={block ? "dashed" : "text"} 
              icon={<PlusOutlined />} 
              onClick={() => add(createNewRow())} 
              size="small"
              block={block}
              className={block ? "rb:mt-1" : ""}
            >
              {block && `+${t('common.add')}`}
            </Button>
          );

          return (
            <>
              {title && (
                <div className="rb:flex rb:items-center rb:mb-2 rb:justify-between">
                  <div className="rb:font-medium">{title}</div>
                  <AddButton />
                </div>
              )}
              
              <Table<TableRow>
                bordered
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
                scroll={{ x: 'max-content' }}
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