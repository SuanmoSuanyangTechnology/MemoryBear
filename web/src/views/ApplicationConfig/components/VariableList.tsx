import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Switch } from 'antd'
import variablesEmpty from '@/assets/images/application/variablesEmpty.svg'
import Card from './Card'
import Table from '@/components/Table';
import type { Variable, VariableEditModalRef } from '../types'
import Empty from '@/components/Empty'
import VariableEditModal from './VariableEditModal'

interface VariableListProps {
  data?: Variable[];
  onUpdate: (data: Variable[]) => void;
}
const VariableList: FC<VariableListProps> = ({data = [], onUpdate}) => {
  const { t } = useTranslation()
  const variableEditModalRef = useRef<VariableEditModalRef>(null)
  const [variableList, setVariableList] = useState<Variable[]>([])
  const [maxIndex, setMaxIndex] = useState(0)

  useEffect(() => {
    if (!data || data.length === 0) return
    const list = data.map((item, index) => ({
      ...item,
      index
    }))
    setVariableList(list)
    onUpdate(list)
    setMaxIndex(list.length)
  }, [data])
  
  const handleAddVariable = () => {
    variableEditModalRef.current?.handleOpen()
  }
  const handleSaveVariable = (value: Variable) => {
    if (value.index !== undefined && value.index >= 0) {
      const index = variableList.findIndex(item => item.index === value.index)
      if (index !== -1) {
        const newData = [...variableList]
        newData[index] = value
        setVariableList([...newData])
        onUpdate([...newData])
      }
    } else {
      const list = [...variableList, {
        index: maxIndex + 1,
        ...value
      }]
      setVariableList(list)
      onUpdate([...list])
      setMaxIndex(maxIndex + 1)
    }
  }
  const handleDeleteVariable = (index: number) => {
    const list = variableList.filter((_, i) => i !== index)
    setVariableList(list)
    onUpdate([...list])
  }
  return (
    <Card title={t('application.variableConfiguration')}>
      <div className="rb:flex rb:items-center rb:justify-between rb:mb-[11px]">
        <div className="rb:font-medium rb:leading-[20px]">
          {t('application.VariableManagement')}
          <span className="rb:font-regular rb:text-[12px] rb:text-[#5B6167]"> ({t('application.VariableManagementDesc')})</span>
        </div>
        <Button style={{padding: '0 8px', height: '24px'}} onClick={handleAddVariable}>+{t('application.addVariables')}</Button>
      </div>

      {/* List */}
      {variableList.length > 0
        ? (
          <div className="rb:mt-[12px]">
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
                      <Button type="link" danger onClick={() => handleDeleteVariable(index)}>
                        {t('common.delete')}
                      </Button>
                    </Space>
                  ),
                },
              ]}
              initialData={variableList as unknown as Record<string, unknown>[]}
              emptySize={88}
            />
          </div>
        )
        : <Empty url={variablesEmpty} size={88} subTitle={t('application.variablesEmpty')} />
      }
      <VariableEditModal
        ref={variableEditModalRef}
        refreshTable={handleSaveVariable}
      />
    </Card>
  )
}
export default VariableList