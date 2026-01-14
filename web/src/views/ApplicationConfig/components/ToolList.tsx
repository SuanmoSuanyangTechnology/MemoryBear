import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, List, Switch } from 'antd'
import Card from './Card'
import type {
  ToolModalRef,
  ToolOption
} from '../types'
import Empty from '@/components/Empty'
import ToolModal from './ToolModal'
import { getToolMethods, getToolDetail } from '@/api/tools'

const ToolList: FC<{ data: ToolOption[]; onUpdate: (config: ToolOption[]) => void}> = ({data, onUpdate}) => {
  const { t } = useTranslation()
  const toolModalRef = useRef<ToolModalRef>(null)
  const [toolList, setToolList] = useState<ToolOption[]>([])
  useEffect(() => {
    if (data) {
      const processedData = data.map(async (item) => {
        if (!item.label && item.tool_id) {
          try {
            const [toolDetail, methods] = await Promise.all([
              getToolDetail(item.tool_id),
              getToolMethods(item.tool_id)
            ])

            switch ((toolDetail as any).tool_type) {
              case 'mcp':
                const mcpFilterItem = (methods as any[]).find(vo => vo.name === item.operation)
                return {
                  ...item,
                  label: mcpFilterItem?.description,
                  method_id: mcpFilterItem?.method_id,
                  value: mcpFilterItem?.name,
                  description: mcpFilterItem?.description,
                  parameters: mcpFilterItem?.parameters
                }
                break
              case 'builtin':
                if ((methods as any[]).length > 1) {
                  const builtinFilterItem = (methods as any[]).find(vo => vo.name === item.operation)
                  return {
                    ...item,
                    label: builtinFilterItem?.description,
                    method_id: builtinFilterItem?.method_id,
                    value: builtinFilterItem?.name,
                    description: builtinFilterItem?.description,
                    parameters: builtinFilterItem?.parameters
                  }
                }
                return {
                  ...item,
                  label: (methods as any[])[0]?.description,
                  method_id: (methods as any[])[0]?.method_id,
                  value: (methods as any[])[0]?.name,
                  description: (methods as any[])[0]?.description,
                  parameters: (methods as any[])[0]?.parameters
                }
                break
              default:
                const customFilterItem = (methods as any[]).find(vo => vo.method_id === item.operation)
                return {
                  ...item,
                  label: customFilterItem?.name,
                  method_id: customFilterItem?.method_id,
                  value: customFilterItem?.name,
                  description: customFilterItem?.description,
                  parameters: customFilterItem?.parameters
                }
            }
          } catch (error) {
            return item
          }
        }
        return item
      })
      
      Promise.all(processedData).then(setToolList)
    }
  }, [data])

  const handleAddTool = () => {
    toolModalRef.current?.handleOpen()
  }
  const updateTools = (tool: ToolOption) => {
    const list = [...toolList, tool]
    setToolList(list)
    onUpdate(list)
  }
  const handleDeleteTool = (index: number) => {
    const list = toolList.filter((_item, idx) => idx !== index)
    setToolList([...list])
    onUpdate(list)
  }
  const handleChangeEnabled = (index: number) => {
    const list = toolList.map((item, idx) => {
      if (idx === index) {
        return {
          ...item,
          enabled: !item.enabled
        }
      }
      return item
    })
    setToolList([...list])
    onUpdate(list)
  }
  return (
    <Card 
      title={t('application.toolConfiguration')}
      extra={
        <Button style={{ padding: '0 8px', height: '24px' }} onClick={handleAddTool}>+{t('application.addTool')}</Button>
      }
    >

      {toolList.length === 0
        ? <Empty size={88} />
        : 
          <List
            grid={{ gutter: 12, column: 1 }}
            dataSource={toolList}
            renderItem={(item, index) => (
              <List.Item>
                <div key={index} className="rb:flex rb:items-center rb:justify-between rb:p-[12px_16px] rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg">
                  <div className="rb:font-medium rb:leading-4">
                    {item.label}
                  </div>
                  <Space size={12}>
                    <div 
                      className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]" 
                      onClick={() => handleDeleteTool(index)}
                    ></div>
                    <Switch checked={item.enabled} onChange={() => handleChangeEnabled(index)} />
                  </Space>
                </div>
              </List.Item>
            )}
          />
      }
      <ToolModal
        ref={toolModalRef}
        refresh={updateTools}
      />
    </Card>
  )
}
export default ToolList