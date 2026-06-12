import { type FC } from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Select, Table, Form, type TableProps, Flex, Checkbox, Input } from 'antd';

import Empty from '@/components/Empty';

export interface TableRow {
  name?: string;
  required?: boolean;
  type?: string;
}

interface EditableTableProps {
  parentName: string | string[];
  title?: string;
  typeOptions?: { value: string, label: string }[]
  size?: "small"
}

const EditableTable: FC<EditableTableProps> = ({
  parentName,
  title,
  typeOptions = [],
  size = 'small'
}) => {
  const { t } = useTranslation();

  const createNewRow = (): TableRow => ({
    name: undefined,
    required: false,
    ...(typeOptions.length > 0 && { type: typeOptions[0].value })
  });

  const getColumns = (remove: (index: number) => void): TableProps<TableRow>['columns'] => {
    const hasType = typeOptions.length > 0;
    const formClassName = 'rb:mb-0! rb:bg-[#F6F6F6] rb:rounded-[8px] rb:py-[2px]! rb:px-[6px]!'

    return [
      {
        title: t('workflow.config.name'),
        dataIndex: 'name',
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item name={[index, 'name']} className={formClassName}>
            <Input
              size={size}
              placeholder={t('common.pleaseEnter')}
              variant="borderless"
            />
          </Form.Item>
        )
      },
      ...(hasType ? [{
        title: t('workflow.config.type'),
        dataIndex: 'type',
        width: 120,
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item shouldUpdate noStyle>
            {(form) => (
              <Form.Item name={[index, 'type']} noStyle>
                <Select 
                  placeholder={t('workflow.config.type')} 
                  options={typeOptions}
                  popupMatchSelectWidth={false}
                  onChange={() => {
                    form.setFieldValue([...Array.isArray(parentName) ? parentName : [parentName], index, 'value'], undefined);
                  }}
                  size={size}
                  variant="borderless"
                  className="rb:w-full! select"
                />
              </Form.Item>
            )}
          </Form.Item>
        )
      }] : []),
      {
        title: t('workflow.config.required'),
        dataIndex: 'required',
        width: 30,
        render: (_: any, __: TableRow, index: number) => (
          <Form.Item name={[index, 'required']} className={formClassName} valuePropName="checked">
            <Checkbox />
          </Form.Item>
        )
      },
      {
        title: '',
        dataIndex: 'actions',
        width: 20,
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