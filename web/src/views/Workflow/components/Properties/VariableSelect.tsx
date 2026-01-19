import { type FC } from 'react'
import clsx from 'clsx';
import { Select, type SelectProps } from 'antd'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
type LabelRender = SelectProps['labelRender'];

interface VariableSelectProps extends SelectProps {
  options: Suggestion[];
  value?: string;
  onChange?: (value: string) => void;
  allowClear?: boolean;
  filterBooleanType?: boolean;
  size?: 'small' | 'middle' | 'large'
}

const VariableSelect: FC<VariableSelectProps> = ({
  placeholder,
  options,
  value,
  allowClear = true,
  onChange,
  size = 'middle',
  filterBooleanType = false,
  ...resetPorps
}) => {

  const handleChange = (value: string) => {
    onChange?.(value);
  }
  const labelRender: LabelRender = (props) => {
    const { value } = props
    const filterOption = filteredOptions.find(vo => `{{${vo.value}}}` === value)

    if (filterOption) {
      return (
        <span
          className={clsx("rb:w-full rb:wrap-break-word rb:line-clamp-1 rb:border rb:border-[#DFE4ED] rb:rounded-md rb:bg-white rb:text-[12px] rb:inline-flex rb:items-center rb:px-1.5 rb:cursor-pointer", {
            'rb:leading-5.5!': size !== 'small',
            'rb:leading-4!': size === 'small'
          })}
          contentEditable={false}
        >
          {filterOption.nodeData?.icon && filterOption.nodeData?.name && (
            <>
              <img
                src={filterOption.nodeData.icon}
                style={{ width: '12px', height: '12px', marginRight: '4px' }}
                alt=""
              />
              {filterOption.nodeData.name}
              <span className="rb:text-[#DFE4ED] rb:mx-0.5">/</span>
            </>
          )}
          <span className="rb:text-[#155EEF]">{filterOption.label}</span>
        </span>
      )
    }
    return null
  }
  const filteredOptions = filterBooleanType 
    ? options.filter(option => option.dataType !== 'boolean')
    : options;

  const groupedSuggestions = filteredOptions.reduce((groups: Record<string, any[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeId = nodeData.id as string;
    if (!groups[nodeId]) {
      groups[nodeId] = [];
    }
    groups[nodeId].push(suggestion);
    return groups;
  }, {});

  const groupedOptions = Object.entries(groupedSuggestions).map(([_nodeId, suggestions]) => ({
    label: suggestions[0].nodeData.name,
    options: suggestions.map(s => ({ 
      label: <div className="rb:flex rb:items-center rb:gap-1 rb:justify-between"> { s.label } <span>{s.dataType}</span></div>, 
      value: `{{${s.value}}}` 
    }))
  }));
  
  return (
    <Select
      {...resetPorps}
      size={size}
      placeholder={placeholder}
      value={value}
      style={{ width: '100%' }}
      options={groupedOptions}
      labelRender={labelRender}
      onChange={handleChange}
      showSearch
      allowClear={allowClear}
      filterOption={(input, option) => {
        if (input === '/') return true;
        if (option?.options) {
          return option.label?.toLowerCase().includes(input.toLowerCase()) ||
                 option.options.some((opt: any) => 
                   opt.value.toLowerCase().includes(input.toLowerCase())
                 );
        }
        return option?.label?.toLowerCase().includes(input.toLowerCase()) ?? false;
      }}
    />
  )
}

export default VariableSelect
