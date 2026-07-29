/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 15:40:13
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-12 12:16:14
 */
import { type FC } from 'react'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import { useVariableSelect } from './useVariableSelect'
import VariableSelectTrigger from './VariableSelectTrigger'
import VariableSelectDropdown from './VariableSelectDropdown'
import VariableSelectChildPanels from './VariableSelectChildPanels'

interface VariableSelectProps {
  options: Suggestion[];
  value?: string | string[];
  allowClear?: boolean;
  filterBooleanType?: boolean;
  multiple?: boolean;
  size?: 'small' | 'middle' | 'large';
  placeholder?: string;
  variant?: 'outlined' | 'borderless' | 'filled';
  className?: string;
  onChange?: (value?: string | string[], option?: Suggestion | Suggestion[] | undefined) => void;
}

const VariableSelect: FC<VariableSelectProps> = ({
  placeholder,
  options,
  value,
  allowClear = true,
  onChange,
  size = 'middle',
  filterBooleanType = false,
  multiple = false,
  variant = 'outlined',
  className,
}) => {
  const state = useVariableSelect({ options, value, multiple, filterBooleanType, onChange });

  return (
    <div ref={state.containerRef} className={`rb:relative rb:w-full rb:min-w-0 rb:max-w-full ${className}`}>
      <VariableSelectTrigger
        state={state}
        value={value}
        multiple={multiple}
        allowClear={allowClear}
        size={size}
        variant={variant}
        placeholder={placeholder}
        className={className}
      />

      {state.open && (
        <>
          <VariableSelectDropdown state={state} value={value} multiple={multiple} />
          {state.expandedPath.length > 0 && (
            <VariableSelectChildPanels state={state} value={value} multiple={multiple} />
          )}
        </>
      )}
    </div>
  );
};

export default VariableSelect
