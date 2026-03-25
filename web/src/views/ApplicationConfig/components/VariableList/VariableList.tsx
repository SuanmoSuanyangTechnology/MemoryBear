/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:32 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 17:10:30
 */
/**
 * Variable List Component
 * Manages application input variables configuration
 * Allows adding, editing, and removing variables
 */

import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Switch, Form } from 'antd'

import variablesEmpty from '@/assets/images/application/variablesEmpty.svg'
import Card from '../Card'
import Table from '@/components/Table';
import type { Variable, VariableEditModalRef } from './types'
import Empty from '@/components/Empty'
import VariableEditModal from './VariableEditModal'

/**
 * Component props
 */
interface VariableListProps {
  /** Current variable list */
  value?: Variable[];
  /** Callback when variables change */
  onChange?: (value: Variable[]) => void;
}

/**
 * Variable list management component
 */const VariableList: FC<VariableListProps> = ({value = [], onChange}) => {
  const { t } = useTranslation()
  const variableEditModalRef = useRef<VariableEditModalRef>(null)
  
  /** Open variable edit modal */
  const handleAddVariable = () => {
    variableEditModalRef.current?.handleOpen()
  }
  /** Save variable changes */
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
      title={t('application.variableConfiguration')}
      extra={
        <Button
          size="small"
          className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-[#21233"
          onClick={handleAddVariable}
        >
          + {t('application.addVariables')}
        </Button>
      }
    >
      <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2">
        <span className="rb:font-medium">{t('application.variableManagement')}</span>
        <span className="rb:font-regular rb:text-[#5B6167]"> ({t('application.variableManagementDesc')})</span>
      </div>

      <Form.List name="variables" initialValue={value}>
        {(fields, { remove }) => {
          return (
            <>
              {fields.length > 0 ? (
                <div className="rb:mt-3">
                  <Table<Variable>
                    size="small"
                    rowKey="index"
                    bordered={true}
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
                        render: (required) => <Switch size="small" checked={!required} disabled />
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
                    initialData={value}
                    emptySize={88}
                  />
                </div>
              ) : (
                <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6"><Empty url={variablesEmpty} size={88} subTitle={t('application.variablesEmpty')} /></div>
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