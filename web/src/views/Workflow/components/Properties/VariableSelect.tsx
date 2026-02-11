/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:40:13 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 15:40:13 
 */
import { type FC } from 'react'
import clsx from 'clsx';
import { Select, type SelectProps } from 'antd'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
type LabelRender = SelectProps['labelRender'];

/**
 * Props for VariableSelect component
 */
interface VariableSelectProps extends SelectProps {
  /** Available variable options */
  options: Suggestion[];
  /** Current selected value */
  value?: string;
  /** Whether to show clear button */
  allowClear?: boolean;
  /** Filter out boolean type variables */
  filterBooleanType?: boolean;
  /** Size of the select component */
  size?: 'small' | 'middle' | 'large'
}

/**
 * VariableSelect component
 * Custom select component for workflow variables with grouped options and custom rendering
 * @param props - Component props
 */
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

  /**
   * Handle value change and pass selected option to parent
   * @param value - Selected value
   */
  const handleChange: SelectProps['onChange'] = (value: string) => {
    const filterItem = options.find(option => `{{${option.value}}}` === value)
    onChange?.(value, filterItem);
  }
  /**
   * Custom label renderer for selected value
   * Displays node icon, name and variable label
   * @param props - Label render props
   */
  const labelRender: LabelRender = (props) => {
    const { value } = props
    const filterOption = filteredOptions.find(vo => `{{${vo.value}}}` === value)

    if (filterOption) {
      return (
        <span
          className={clsx("rb:max-w-full rb:wrap-break-word rb:line-clamp-1 rb:border rb:border-[#DFE4ED] rb:rounded-md rb:bg-white rb:text-[12px] rb:inline-flex rb:items-center rb:px-1.5 rb:cursor-pointer", {
            'rb:leading-5.5!': size !== 'small',
            'rb:leading-4! rb:text-[10px]!': size === 'small'
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
  // Filter options based on boolean type if needed
  const filteredOptions = filterBooleanType 
    ? options.filter(option => option.dataType !== 'boolean')
    : options;

  /**
   * Group suggestions by node ID
   */
  const groupedSuggestions = filteredOptions.reduce((groups: Record<string, any[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeId = nodeData.id as string;
    if (!groups[nodeId]) {
      groups[nodeId] = [];
    }
    groups[nodeId].push(suggestion);
    return groups;
  }, {});

  /**
   * Format grouped options for Select component
   */
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
      optionFilterProp="value"
      filterOption={(input, option) => {
        if (input === '/') return true;
        const value = 'value' in option! ? option.value as string : '';
        return value.toLowerCase().includes(input.toLowerCase());
      }}
    />
  )
}

export default VariableSelect
