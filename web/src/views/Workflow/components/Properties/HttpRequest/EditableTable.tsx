import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Select, Table } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import Editor from '../../Editor';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin';
import Empty from '@/components/Empty';
import VariableSelect from '../VariableSelect';

export interface TableRow {
  key: string;
  name: string;
  value: string;
  type?: string;
}

interface EditableTableProps {
  title?: string;
  value?: Record<string, string> | TableRow[];
  onChange?: (value: TableRow[]) => void;
  options?: Suggestion[];
  typeOptions?: {value: string, label: string}[]
}

const EditableTable: React.FC<EditableTableProps> = ({
  title,
  value,
  onChange,
  options = [],
  typeOptions = []
}) => {
  const { t } = useTranslation()
  const [rows, setRows] = useState<TableRow[]>([]);

  useEffect(() => {
    if (Array.isArray(value)) {
      setRows([...value])
    } else if (value && Object.keys(value).length > 0) {
      setRows(Object.entries(value).map(([key, val], index) => ({
        key: index.toString(),
        name: key || '',
        value: val || '',
        type: typeOptions.length > 0 ? typeOptions[0].value : undefined
      })))
    } else {
      setRows([])
    }
  }, [value, typeOptions])

  const handleChange = (key: string, field: 'name' | 'value' | 'type', val: string) => {
    const newRows = rows.map(row => 
      row.key === key ? { ...row, [field]: val } : row
    );
    setRows(newRows);
    onChange?.(newRows);
  };

  const handleAdd = () => {
    const newRow: TableRow = {
      key: Date.now().toString(),
      name: '',
      value: '',
      ...(typeOptions.length > 0 && { type: typeOptions[0].value })
    };
    const newRows = [...rows, newRow];
    setRows(newRows);
    onChange?.(newRows);
  };

  const handleDelete = (key: string) => {
    const newRows = rows.filter(row => row.key !== key);
    setRows(newRows);
    onChange?.(newRows);
  };

  const columns = useMemo(() => {
    const baseColumns = [
      {
        title: typeOptions.length > 0 ? t('workflow.config.name') : '键',
        dataIndex: 'name',
        width: typeOptions.length > 0 ? '35%' : '45%',
        render: (text: string, record: TableRow) => (
          <Editor
            options={options}
            value={text}
            height={32}
            variant="outlined"
            onChange={(value) => handleChange(record.key, 'name', value || '')}
          />
        ),
      }
    ];

    if (typeOptions.length > 0) {
      baseColumns.push({
        title: t('workflow.config.type'),
        dataIndex: 'type',
        width: '20%',
        render: (text: string, record: TableRow) => (
          <Select
            value={text}
            options={typeOptions}
            onChange={(value) => handleChange(record.key, 'type', value)}
          />
        ),
      });
    }

    baseColumns.push({
      title: typeOptions.length > 0 ? t('workflow.config.value') : '值',
      dataIndex: 'value',
      width: typeOptions.length > 0 ? '35%' : '45%',
      render: (text: string, record: TableRow) => {
        if (record.type === 'file') {
          return (
            <VariableSelect
              options={options}
              value={text}
              onChange={(value) => handleChange(record.key, 'value', value || '')}
            />
          )
        }
        return (
          <Editor
            options={options}
            value={text}
            height={32}
            variant="outlined"
            onChange={(value) => handleChange(record.key, 'value', value || '')}
          />
        )
      },
    });

    baseColumns.push({
      title: '',
      dataIndex: 'actions',
      width: '10%',
      render: (_: any, record: TableRow) => (
        <Button
          type="text"
          icon={<DeleteOutlined />}
          onClick={() => handleDelete(record.key)}
        />
      ),
    });

    return baseColumns;
  }, [typeOptions, options, t]);

  return (
    <div className="rb:mb-4">
      {title && (
        <div className="rb:flex rb:items-center rb:mb-2 rb:justify-between">
          <div className="rb:font-medium">{title}</div>
          <Button
            type="text"
            icon={<PlusOutlined />}
            onClick={handleAdd}
            size="small"
          />
        </div>
      )}
      <Table
        columns={columns}
        dataSource={rows}
        pagination={false}
        size="small"
        locale={{ emptyText: <Empty size={88} /> }}
        scroll={{ x: 'max-content' }}
      />
      {!title && (
        <Button type="dashed" onClick={handleAdd} block className='rb:mt-1'>
          +{t('common.add')}
        </Button>
      )}
    </div>
  );
};

export default EditableTable;