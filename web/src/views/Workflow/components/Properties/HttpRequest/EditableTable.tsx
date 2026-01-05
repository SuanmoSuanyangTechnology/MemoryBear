import { useState, useEffect } from 'react';
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
    console.log('EditableTable value', value)
    if (Array.isArray(value)) {
      setRows([...value])
    } else if (value && Object.keys(value).length > 0) {
      // Only update if rows are empty or significantly different
      const valueEntries = Object.entries(value)
      if (rows.length === 0 || rows.length !== valueEntries.length) {
        setRows(valueEntries.map(([key, val], index) => {
          console.log('val', val)
          return {
            key: index.toString(),
            name: key || '',
            value: val || '',
            type: typeOptions.length > 0 ? typeOptions[0].value : undefined
          }
        }))
      }
    } else {
      setRows([])
    }
  }, [JSON.stringify(value), typeOptions.length])

  const handleChange = (key: string, field: 'name' | 'value' | 'type', val: string) => {
    const newRows = [...rows.map(row => 
      row.key === key ? { ...row, [field]: val } : row
    )];

    setRows(newRows);
    onChange?.(newRows);
  };

  const handleAdd = () => {
    const newKey = Date.now().toString();
    if (typeOptions.length) {
      setRows([...rows, { key: newKey, name: '', value: '', type: typeOptions[0].value }]);
    } else {
      setRows([...rows, { key: newKey, name: '', value: '' }]);
    }
  };

  const handleDelete = (key: string, index: number) => {
    console.log('index', index)

    if (rows.length === 1) {
      setRows([]);
      onChange?.([]);
    } else {
      const newRows = rows.filter(row => row.key !== key);
      setRows(newRows);
      onChange?.(newRows);
    }
  };

  const columns = typeOptions?.length > 0 ? [
    {
      title: t('workflow.config.name'),
      dataIndex: 'name',
      width: '45%',
      render: (text: string, record: TableRow) => (
        <Editor
          options={options}
          value={text}
          height={32}
          variant="outlined"
          onChange={(value) => handleChange(record.key, 'name', value)}
        />
      ),
    },
    {
      title: t('workflow.config.type'),
      dataIndex: 'type',
      width: '20%',
      render: (text: string, record: TableRow) => (
        <Select
          value={text}
          options={typeOptions}
          onChange={(value) => {
            console.log('value record', value)
            handleChange(record.key, 'type', value)
          }}
        />
      ),
    },
    {
      title: t('workflow.config.value'),
      dataIndex: 'value',
      width: '45%',
      render: (text: string, record: TableRow) => {
        if (record.type === 'file') {
          
          return (
            <VariableSelect
              options={options}
              value={text}
              onChange={(value) => {
                console.log('value record', value)
                handleChange(record.key, 'value', value)
              }}
            />
          )
        }
        return (
          <Editor
            options={options}
            value={text}
            height={32}
            variant="outlined"
            onChange={(value) => {
              console.log('value record', value)
              handleChange(record.key, 'value', value)
            }}
          />
        )
      },
    },
    {
      title: '',
      width: '10%',
      render: (_: any, record: TableRow, index: number) => (
        <Button
          type="text"
          icon={<DeleteOutlined />}
          onClick={() => handleDelete(record.key, index)}
        />
      ),
    },
  ] : [
    {
      title: '键',
      dataIndex: 'name',
      width: '45%',
      render: (text: string, record: TableRow) => (
        <Editor
          options={options}
          value={text}
          height={32}
          variant="outlined"
          onChange={(value) => handleChange(record.key, 'name', value)}
        />
      ),
    },
    {
      title: '值',
      dataIndex: 'value',
      width: '45%',
      render: (text: string, record: TableRow) => (
        <Editor
          options={options}
          value={text}
          height={32}
          variant="outlined"
          onChange={(value) => handleChange(record.key, 'value', value)}
        />
      ),
    },
    {
      title: '',
      width: '10%',
      render: (_: any, record: TableRow, index: number) => (
        <Button
          type="text"
          icon={<DeleteOutlined />}
          onClick={() => handleDelete(record.key, index)}
        />
      ),
    },
  ];

  return (
    <div className="rb:mb-4">
      {title && <div className="rb:flex rb:items-center rb:mb-2 rb:justify-between">
        <div className="rb:font-medium">{title}</div>
        <Button
          type="text"
          icon={<PlusOutlined />}
          onClick={handleAdd}
          size="small"
        />
      </div>}
      <Table
        columns={columns}
        dataSource={rows}
        pagination={false}
        size="small"
        locale={{ emptyText: <Empty size={88} /> }}
        scroll={{ x: 'max-content' }}
      />
      {!title &&
        <Button type="dashed" onClick={handleAdd} block className='rb:mt-1'>
          +{t('common.add')}
        </Button>
      }
    </div>
  );
};

export default EditableTable;