import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Space, List } from 'antd'
import Empty from '@/components/Empty'

import type { ParamItem, ParamEditModalRef } from './types'
import ParamEditModal from './ParamEditModal'
interface ParamsListProps {
  label: string;
  value?: ParamItem[];
  onChange?: (value: ParamItem[]) => void
}

const ParamsList: FC<ParamsListProps> = ({
  label,
  value = [],
  onChange
}) => {
  const { t } = useTranslation()
  const paramEditModalRef = useRef<ParamEditModalRef>(null)

  const handleAdd = () => {
    paramEditModalRef.current?.handleOpen()
  }
  const handleEdit = (index: number) => {
    paramEditModalRef.current?.handleOpen(value[index], index)
  }
  const handleDelete = (index: number) => {
    const list = [...value]
    list.splice(index, 1)
    onChange && onChange(list)
  }
  const handleSave = (vo: ParamItem, index?: number) => {
    if (index !== undefined) {
      const list = [...value]
      list[index] = vo
      onChange && onChange(list)
    } else {
      onChange && onChange([...value, vo])
    }
  }
  return (
    <div>
      <div className="rb:flex rb:justify-between rb:items-center">
        <div>{label}</div>

        <Space>
          <Button style={{ padding: '0 8px', height: '24px' }} onClick={handleAdd}>+</Button>
        </Space>
      </div>

      {value?.length === 0
        ? <Empty size={88} />
        :
        <List
          grid={{ gutter: 12, column: 1 }}
          dataSource={value}
          renderItem={(item, index) => (
            <List.Item>
              <div key={index} className="rb:group rb:relative rb:p-[12px_16px] rb:bg-[#FBFDFF] rb:cursor-pointer rb:border rb:border-[#DFE4ED] rb:rounded-lg">
                <div className="rb:flex rb:items-center rb:justify-between">
                  <div className="rb:leading-4">
                    <span className="rb:font-medium">{item.name}</span>
                    <span className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular"> ({t(`workflow.config.parameter-extractor.${item.type}`)})</span>
                  </div>
                  <span className="rb:block rb:group-hover:hidden rb:text-[12px] rb:text-[#5B6167] rb:font-regular">{item.required ? t('workflow.config.parameter-extractor.required') : ''}</span>

                </div>
                <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:wrap-break-word rb:line-clamp-1">{item.desc}</div>
                <Space size={12} className="rb:hidden! rb:group-hover:flex! rb:absolute rb:right-4 rb:top-[50%] rb:transform-[translateY(-50%)] rb:bg-white">
                  <div
                    className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
                    onClick={() => handleEdit(index)}
                  ></div>
                  <div
                    className="rb:size-5 rb:cursor-pointer rb:bg-cover  rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                    onClick={() => handleDelete(index)}
                  ></div>
                </Space>
              </div>
            </List.Item>
          )}
        />
      }

      <ParamEditModal
        ref={paramEditModalRef}
        refresh={handleSave}
      />
    </div>
  )
}

export default ParamsList