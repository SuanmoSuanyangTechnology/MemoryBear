import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Space } from 'antd'


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
      <div className="rb:leading-4.25 rb:text-[12px] rb:font-medium rb:mb-2">
        {label}
      </div>

      <Space size={10} direction="vertical" className="rb:w-full!">
        <Button type="dashed" block size="middle" className="rb:text-[12px]!" onClick={handleAdd}>+ {t('workflow.config.parameter-extractor.addParams')}</Button>

        {value?.map((item, index) => (
          <div
            key={index}
            className="rb:cursor-pointer rb:group rb:py-2 rb:pl-2.5 rb:pr-2 rb:text-[12px] rb:flex rb:items-center rb:justify-between rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-md"
          >
            <div>
              <span className="rb:font-medium">{item.name}</span>
              <span className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular"> ({t(`workflow.config.parameter-extractor.${item.type}`)}) {item.required ? t('workflow.config.parameter-extractor.required') : ''}</span>
              <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4.25 rb:mt-0.5">{item.desc}</div>
            </div>

            <Space size={8}>
              <div
                className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]"
                onClick={() => handleEdit(index)}
              ></div>
              <div
                className="rb:size-4 rb:cursor-pointer rb:bg-cover  rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]"
                onClick={() => handleDelete(index)}
              ></div>
            </Space>
          </div>
        ))}
      </Space>

      <ParamEditModal
        ref={paramEditModalRef}
        refresh={handleSave}
      />
    </div>
  )
}

export default ParamsList