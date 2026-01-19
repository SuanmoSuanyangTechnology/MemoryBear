import { useTranslation } from 'react-i18next'
import { Button, Select, Table, Form, type TableProps } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
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
  size?: "small"
}

const EditableTable: React.FC<EditableTableProps> = ({
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

  const getColumns = (remove: (index: number) => void): TableProps<TableRow>['columns'] => {
    const hasType = typeOptions.length > 0;
    const cellClassName="rb:p-1!"
    const contentClassName ="rb:w-[108px]! rb:text-[12px]!"

    return [
      {
        title: t('workflow.config.name'),
        dataIndex: 'name',
        className: cellClassName,
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item name={[index, 'name']} noStyle>
            <VariableSelect 
              placeholder={t('common.pleaseSelect')} 
              // size="small" 
              options={options}
              filterBooleanType={filterBooleanType}
              popupMatchSelectWidth={false}
              className={contentClassName}
              size={size}
            />
          </Form.Item>
        )
      },
      ...(hasType ? [{
        title: t('workflow.config.type'),
        dataIndex: 'type',
        width: '20%',
        className: cellClassName,
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
                />
              </Form.Item>
            )}
          </Form.Item>
        )
      }] : []),
      {
        title: t('workflow.config.value'),
        dataIndex: 'value',
        className: cellClassName,
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
                    // size="small" 
                    options={filteredOptions}
                    filterBooleanType={filterBooleanType}
                    popupMatchSelectWidth={false}
                    className={contentClassName}
                    size={size}
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
        className: cellClassName,
        render: (_: any, __: TableRow, index: number) => (
          <div
            className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
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
              icon={block ? undefined : <PlusOutlined />} 
              onClick={() => add(createNewRow())} 
              size="small"
              className={block ? "rb:mt-1 rb:text-[12px]! rb:bg-transparent!" : "rb:text-[12px]!"}
            >
              {block && `+${t('common.add')}`}
            </Button>
          );

          return (
            <>
              {title && (
                <div className="rb:flex rb:items-center rb:mb-2 rb:justify-between">
                  <div className="rb:font-medium rb:text-[12px] rb:leading-4.5">{title}</div>
                  <AddButton block={true} />
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
                rowClassName="rb:p-0! rb:bg-[#F6F8FC]!"
                locale={{ emptyText: <Empty size={88} /> }}
                style={{ width: '274px' }}
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