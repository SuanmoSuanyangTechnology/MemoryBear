import { type FC, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Node } from '@antv/x6';
import { Space, Button, Divider, App } from 'antd'
import type { Variable, VariableEditModalRef } from './types'
import type { NodeConfig } from '../../../types'
import VariableEditModal from './VariableEditModal'

interface VariableListProps {
  selectedNode?: Node | null; 
  config: NodeConfig;
  value?: Variable[];
  parentName: string;
  onChange?: (value: Variable[]) => void;
}
const VariableList: FC<VariableListProps> = ({
  value = [], 
  onChange, 
  selectedNode, 
  config, 
  parentName 
}) => {
  const { t } = useTranslation()
  const { modal } = App.useApp()
  const variableModalRef = useRef<VariableEditModalRef>(null)
  const [editIndex, setEditIndex] = useState<number | null>(null)

  const handleAddVariable = () => {
    setEditIndex(null)
    variableModalRef.current?.handleOpen()
  }
  const handleEditVariable = (index: number, vo: Variable) => {
    variableModalRef.current?.handleOpen(vo)
    setEditIndex(index)
  }
  const handleRefreshVariable = (variable: Variable) => {
    if (!selectedNode) return

    if (editIndex !== null) {
      const list = [...value]
      list[editIndex] = variable
      onChange?.(list)
    } else {
      console.log('VariableList', value, variable)
      onChange?.([...value, variable])
    }
  }
  const handleDeleteVariable = (index: number, vo: Variable, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!selectedNode) return

    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: vo.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        const list = [...value]
        list.splice(index, 1)
        onChange?.([...list])
      }
    })
  }
  return (
    <div>
      <Space size={10} direction="vertical" className="rb:w-full">
        <div className="rb:leading-4.25 rb:text-[12px] rb:font-medium">
          {t(`workflow.config.${selectedNode?.data?.type}.${parentName}`)}
        </div>
        <Button type="dashed" block size="middle" className="rb:text-[12px]!" onClick={handleAddVariable}>+ {t('workflow.config.addVariable')}</Button>
        {Array.isArray(value) && value?.map((vo, index) =>
          <div 
            key={`${vo.name}}-${index}`} 
            className="rb:cursor-pointer rb:group rb:py-2 rb:pl-2.5 rb:pr-2 rb:text-[12px] rb:flex rb:items-center rb:justify-between rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-md"
            onClick={() => handleEditVariable(index, vo)}
          >
            <span className="rb:font-medium">{vo.name}·{vo.description}</span>

            <Space size={8}>
              {vo.required && <span className="rb:py-px rb:px-2 rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-sm">{t('workflow.config.start.required')}</span>}
              <span className="rb:py-px rb:px-2 rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-sm">{vo.type}</span>
              <div
                className="rb:size-3 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/close.svg')] rb:hover:bg-[url('@/assets/images/close_hover.svg')]"
                onClick={(e) => handleDeleteVariable(index, vo, e)}
              ></div>
            </Space>
          </div>
        )}

      </Space>
      <Divider size="small" />
      <Space size={10} direction="vertical" className="rb:w-full">
        {config.sys?.map((vo, index) =>
          <div key={index} className="rb:py-2 rb:pl-2.5 rb:pr-2 rb:text-[12px] rb:flex rb:items-center rb:justify-between rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-md">
            <span className="rb:font-medium">sys.{vo.name}</span>
            <span className="rb:py-px rb:px-2 rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-sm">{vo.type}</span>
          </div>
        )}
      </Space>
      <VariableEditModal
        ref={variableModalRef}
        refresh={handleRefreshVariable}
      />
    </div>
  )
}
export default VariableList