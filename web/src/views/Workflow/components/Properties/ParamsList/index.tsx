import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Flex } from 'antd'

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
        <span className="rb:text-[#ff5d34] rb:text-[14px] rb:font-[SimSun,sans-serif] rb:mr-1">*</span>{label}
      </div>

      <Flex gap={10} vertical>
        <Button type="dashed" block size="middle" className="rb:text-[12px]!" onClick={handleAdd}>+ {t('workflow.config.parameter-extractor.addParams')}</Button>

        {value?.map((item, index) => (
          <div
            key={index}
            className="rb:cursor-pointer rb:group rb:py-2 rb:pl-2.5 rb:pr-2 rb:text-[12px] rb-border rb:rounded-md rb:relative"
          >
            <Flex align="center" className="rb:leading-4 rb:w-full! rb:overflow-hidden rb:whitespace-nowrap rb:text-ellipsis rb:line-clamp-2" gap={2}>
              <span className="rb:font-medium rb:inline-block">{item.name}</span>
              <span className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular">({t(`workflow.config.parameter-extractor.${item.type}`)}) {item.required ? t('workflow.config.parameter-extractor.required') : ''}</span>
            </Flex>
            <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4.25 rb:mt-1">{item.desc}</div>

            <Flex gap={10} align="center" justify="end" className="rb:hidden! rb:group-hover:flex! rb:absolute rb:w-22 rb:pr-3! rb:right-0 rb:top-0 rb:bottom-0 rb:bg-[linear-gradient(90deg,rgba(255,255,255,0.5)_0%,#FFFFFF_50%)] rb:shadow-[0px_2px_4px_0px rgba(0,0,0,0.06)] rb:rounded-[0px_8px_8px_0px]">
              <div
                className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]"
                onClick={() => handleEdit(index)}
              ></div>
              <div
                className="rb:size-4 rb:cursor-pointer rb:bg-cover  rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]"
                onClick={() => handleDelete(index)}
              ></div>
            </Flex>
          </div>
        ))}
      </Flex>

      <ParamEditModal
        ref={paramEditModalRef}
        refresh={handleSave}
      />
    </div>
  )
}

export default ParamsList