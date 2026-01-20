import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Switch, Form } from 'antd'
import variablesEmpty from '@/assets/images/application/variablesEmpty.svg'
import Card from '../Card'
import Table from '@/components/Table';
import type { Variable, VariableEditModalRef } from './types'
import Empty from '@/components/Empty'
import VariableEditModal from './VariableEditModal'

interface VariableListProps {
  value?: Variable[];
  onChange?: (value: Variable[]) => void;
}
const VariableList: FC<VariableListProps> = ({value = [], onChange}) => {
  const { t } = useTranslation()
  const variableEditModalRef = useRef<VariableEditModalRef>(null)
  
  const handleAddVariable = () => {
    variableEditModalRef.current?.handleOpen()
  }
  const handleSaveVariable = (variable: Variable) => {
    const newList = [...(value || [])]
    if (variable.index !== undefined && variable.index >= 0) {
      const index = newList.findIndex(item => item.index === variable.index)
      if (index !== -1) {
        newList[index] = variable
      }
    } else {
      newList.push({ ...variable, index: Date.now() })
    }
    onChange?.(newList)
  }
  return (
    <Card
      title={<>
        {t('application.variableConfiguration')}
        <span className="rb:font-regular rb:text-[12px] rb:text-[#5B6167]"> ({t('application.VariableManagementDesc')})</span>
      </>}
      extra={<Button style={{ padding: '0 8px', height: '24px' }} onClick={handleAddVariable}>+ {t('application.addVariables')}</Button>}
    >
      <Form.List name="variables" initialValue={value}>
        {(fields, { remove }) => {
          return (
            <>
              {fields.length > 0 ? (
                <div className="rb:mt-3">
                  <Table
                    rowKey="index"
                    pagination={false}
                    columns={[
                      {
                        title: t('application.variableType'),
                        dataIndex: 'type',
                        key: 'type',
                        render: (type) => t(`application.${type}`)
                      },
                      {
                        title: t('application.variableKey'),
                        dataIndex: 'name',
                        key: 'name',
                      },
                      {
                        title: t('application.variableName'),
                        dataIndex: 'display_name',
                        key: 'display_name',
                      },
                      {
                        title: t('application.optional'),
                        dataIndex: 'required',
                        key: 'required',
                        render: (required) => <Switch checked={!required} disabled />
                      },
                      {
                        title: t('common.operation'),
                        key: 'action',
                        render: (_, record, index: number) => (
                          <Space size="middle">
                            <Button
                              type="link"
                              onClick={() => variableEditModalRef.current?.handleOpen(record as Variable)}
                            >
                              {t('common.edit')}
                            </Button>
                            <Button type="link" danger onClick={() => remove(index)}>
                              {t('common.delete')}
                            </Button>
                          </Space>
                        ),
                      },
                    ]}
                    initialData={value as unknown as Record<string, unknown>[]}
                    emptySize={88}
                  />
                </div>
              ) : (
                <Empty url={variablesEmpty} size={88} subTitle={t('application.variablesEmpty')} />
              )}
            </>
          )
        }}
      </Form.List>
      <VariableEditModal
        ref={variableEditModalRef}
        refreshTable={handleSaveVariable}
      />
    </Card>
  )
}
export default VariableList