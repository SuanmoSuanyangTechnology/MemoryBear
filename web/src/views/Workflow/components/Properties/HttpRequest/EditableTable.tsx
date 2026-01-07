import { useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Select, Table, Form, type TableProps } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';
import Empty from '@/components/Empty';
import VariableSelect from '../VariableSelect';

interface EditableCellProps extends React.HTMLAttributes<HTMLElement> {
  name?: string | string[];
  inputType?: 'select' | 'variableSelect';
  options?: { value: string, label: string }[] | Suggestion[];
}

const EditableCell: React.FC<React.PropsWithChildren<EditableCellProps>> = ({ 
  name, 
  inputType, 
  options, 
  children,
  ...restProps 
}) => {
  const { t } = useTranslation();
  
  if (!inputType) return <td {...restProps}>{children}</td>;
  
  return (
    <td {...restProps}>
      <Form.Item name={name} style={{ margin: 0 }}>
        {inputType === 'select' ? (
          <Select 
            placeholder={t('common.pleaseSelect')} 
            size="small" 
            options={options as { value: string, label: string }[]} 
          />
        ) : (
          <VariableSelect 
            placeholder={t('common.pleaseSelect')} 
            size="small" 
            options={(options as Suggestion[]) || []} 
          />
        )}
      </Form.Item>
    </td>
  );
};

export interface TableRow {
  key: string;
  name?: string;
  value?: string;
  type?: string;
}

interface EditableTableProps {
  parentName: string | string[];
  title?: string;
  options?: Suggestion[];
  typeOptions?: { value: string, label: string }[]
}

const EditableTable: React.FC<EditableTableProps> = ({
  parentName,
  title,
  options = [],
  typeOptions = []
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const values = Form.useWatch(typeof parentName === 'string' ? [parentName] : parentName, form);

  const createNewRow = (): TableRow => ({
    key: Date.now().toString(),
    name: undefined,
    value: undefined,
    ...(typeOptions.length > 0 && { type: typeOptions[0].value })
  });

  const handleAdd = useCallback(() => {
    form.setFieldValue(parentName, [...(values ?? []), createNewRow()]);
  }, [form, parentName, values, typeOptions]);

  const handleDelete = useCallback((index: number) => {
    const currentValues = form.getFieldValue(parentName) || [];
    form.setFieldValue(parentName, currentValues.filter((_: TableRow, i: number) => i !== index));
  }, [form, parentName]);

  const createColumn = (dataIndex: string, inputType: 'select' | 'variableSelect', width: string, columnOptions: any[]) => ({
    title: t(`workflow.config.${dataIndex}`),
    dataIndex,
    width,
    onCell: (_: TableRow, index?: number) => ({
      name: typeof parentName === 'string' ? [parentName, index ?? 0, dataIndex] : [...parentName, index ?? 0, dataIndex],
      inputType,
      options: columnOptions
    } as any)
  });

  const columns: TableProps<TableRow>['columns'] = useMemo(() => {
    const hasType = typeOptions.length > 0;
    const baseWidth = hasType ? '35%' : '45%';

    return [
      createColumn('name', 'variableSelect', baseWidth, options),
      ...(hasType ? [createColumn('type', 'select', '20%', typeOptions)] : []),
      createColumn('value', 'variableSelect', baseWidth, options),
      {
        title: '',
        dataIndex: 'actions',
        width: '10%',
        render: (_: any, __: TableRow, index: number) => (
          <Button type="text" icon={<DeleteOutlined />} onClick={() => handleDelete(index)} />
        )
      }
    ];
  }, [typeOptions, options, t, parentName, handleDelete]);

  const AddButton = ({ block = false }: { block?: boolean }) => (
    <Button 
      type={block ? "dashed" : "text"} 
      icon={<PlusOutlined />} 
      onClick={handleAdd} 
      size="small"
      block={block}
      className={block ? "rb:mt-1" : ""}
    >
      {block && `+${t('common.add')}`}
    </Button>
  );

  return (
    <div className="rb:mb-4">
      {title && (
        <div className="rb:flex rb:items-center rb:mb-2 rb:justify-between">
          <div className="rb:font-medium">{title}</div>
          <AddButton />
        </div>
      )}
      
      <Form.Item name={parentName}>
        <Table<TableRow>
          components={{ body: { cell: EditableCell } }}
          bordered
          dataSource={values}
          columns={columns}
          pagination={false}
          size="small"
          locale={{ emptyText: <Empty size={88} /> }}
          scroll={{ x: 'max-content' }}
        />
      </Form.Item>
      
      {!title && <AddButton block />}
    </div>
  );
};

export default EditableTable;