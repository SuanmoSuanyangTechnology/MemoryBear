import { type FC } from 'react'
import { Select, type SelectProps } from 'antd'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
type LabelRender = SelectProps['labelRender'];

interface VariableSelectProps extends SelectProps {
  options: Suggestion[];
  value?: string;
  onChange?: (value: string) => void;
}

const VariableSelect: FC<VariableSelectProps> = ({
  placeholder,
  options,
  value,
  onChange,
}) => {

  const handleChange = (value: string) => {
    onChange?.(value);
  }
  const labelRender: LabelRender = (props) => {
    const { value } = props
    const filterOption = options.find(vo => vo.value === value)

    if (filterOption) {
      return (
        <span
          className="rb:border rb:border-[#DFE4ED] rb:rounded-md rb:bg-white rb:leading-5.5! rb:text-[12px] rb:inline-flex rb:items-center rb:px-1.5 rb:cursor-pointer"
          contentEditable={false}
        >
          <img
            src={filterOption.nodeData?.icon}
            style={{ width: '12px', height: '12px', marginRight: '4px' }}
            alt=""
          />
          {filterOption.nodeData?.name}
          <span className="rb:text-[#DFE4ED] rb:mx-0.5">/</span>
          <span className="rb:text-[#155EEF]">{filterOption.label}</span>
        </span>
      )
    }
    return null
  }
  const groupedSuggestions = options.reduce((groups: Record<string, any[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeId = nodeData.id as string;
    if (!groups[nodeId]) {
      groups[nodeId] = [];
    }
    groups[nodeId].push(suggestion);
    return groups;
  }, {});

  const groupedOptions = Object.entries(groupedSuggestions).map(([nodeId, suggestions]) => ({
    label: suggestions[0].nodeData.name,
    options: suggestions.map(s => ({ label: s.label, value: s.value }))
  }));
  
  return (
    <Select
      placeholder={placeholder}
      value={value}
      style={{ width: '100%' }}
      options={groupedOptions}
      labelRender={labelRender}
      onChange={handleChange}
      showSearch
      filterOption={(input, option) => {
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
