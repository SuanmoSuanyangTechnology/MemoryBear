/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:01:59 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:46:05
 */

/**
 * ButtonCheckbox - A custom checkbox component styled as a button
 * 
 * This component provides a button-like interface for checkbox functionality,
 * with support for custom icons and visual states (checked/unchecked).
 * 
 * @component
 */

import { type FC, type ReactNode, useEffect } from 'react';
import { type RadioGroupProps } from 'antd';
import clsx from 'clsx'

// Button checkbox component props
interface ButtonCheckboxProps extends Omit<RadioGroupProps, 'onChange'> {
  /** Whether the checkbox is checked */
  checked?: boolean;
  /** Callback fired when value changes (for side effects) */
  onValueChange?: (checked: boolean) => void;
  /** Callback fired when checkbox state changes */
  onChange?: (checked: boolean) => void;
  /** Icon path for unchecked state */
  icon?: string;
  /** Icon path for checked state */
  checkedIcon?: string;
  /** Button content */
  children?: ReactNode
}

const ButtonCheckbox: FC<ButtonCheckboxProps> = ({
  checked = false,
  onValueChange,
  onChange,
  icon,
  checkedIcon,
  children,
}) => {
  // Listen to value changes and trigger side effects via onValueChange callback
  useEffect(() => {
    if (onValueChange) {
      onValueChange(checked);
    }
  }, [checked, onValueChange]);

  // Toggle checked state when button is clicked
  const handleChange = () => {
    if (onChange) {
      onChange(!checked);
    }
  }
  
  return (
    <div 
      className={clsx("rb:flex rb:items-center rb:border rb:rounded-lg rb:px-2 rb:text-[12px] rb:h-6 rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
        // Checked state: blue background and border
        "rb:bg-[rgba(21,94,239,0.06)] rb:border-[#155EEF] rb:text-[#155EEF]": checked,
        // Unchecked state: gray border and dark text
        "rb:border-[#DFE4ED] rb:text-[#212332]": !checked,
      })} 
      onClick={handleChange}
    >
      {/* Display unchecked icon when not checked */}
      {icon && !checked && <img src={icon} className="rb:w-4 rb:h-4 rb:mr-1" />}
      {/* Display checked icon when checked */}
      {checkedIcon && checked && <img src={checkedIcon} className="rb:w-4 rb:h-4 rb:mr-1" />}
      {children}
    </div>
  );
};

export default ButtonCheckbox;