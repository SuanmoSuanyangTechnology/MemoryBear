/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:19:30 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 10:11:37
 */
/**
 * RadioGroupCard Component
 * 
 * A radio group component that displays options as selectable cards with:
 * - Visual card-based selection interface
 * - Optional icons and descriptions
 * - Support for clear selection
 * - Block or inline layout modes
 * - Custom item rendering
 * 
 * @component
 */

import { type FC, type Key, type ReactNode, useEffect } from 'react';
import { type RadioGroupProps } from 'antd';
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';

/** Radio card option interface */
interface RadioCardOption {
  /** Option value */
  value: string | number | boolean | null | undefined | Key;
  /** Option label text */
  label: string;
  /** Optional description text */
  labelDesc?: string;
  /** Optional icon URL */
  icon?: string;
  /** Whether the option is disabled */
  disabled?: boolean;
  /** Whether the option is recommended */
  recommend?: boolean;
  /** Additional properties */
  [key: string]: string | number | boolean | undefined | null | Key;
}

/** Props interface for RadioGroupCard component */
interface RadioCardProps extends Omit<RadioGroupProps, 'onChange'> {
  /** Array of radio card options */
  options: RadioCardOption[];
  /** Callback fired when value changes (for side effects) */
  onValueChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  /** Callback fired when selection changes */
  onChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  /** Custom render function for each option */
  itemRender?: (option: RadioCardOption) => ReactNode;
  /** Whether clicking selected option clears selection */
  allowClear?: boolean;
  /** Whether to display cards in block (vertical) layout */
  block?: boolean;
}

/** Radio group card component that displays options as selectable cards */
const RadioGroupCard: FC<RadioCardProps> = ({
  options,
  value,
  onValueChange,
  onChange,
  itemRender,
  allowClear = true,
  block = false,
}) => {
  const { t } = useTranslation();
  /** Listen to value changes and trigger side effects via onValueChange callback */
  useEffect(() => {
    if (onValueChange) {
      onValueChange(value);
    }
  }, [value, onValueChange]);

  /** Handle option selection with support for clear and disabled states */
  const handleChange = (option: RadioCardOption) => {
    // Ignore clicks on disabled options
    if (option.disabled) return
    if (onChange) {
      // Clear selection if allowClear is true and option is already selected
      if (allowClear && value === option.value) {
        onChange(null, undefined);
      } else {
        onChange(String(option.value), option);
      }
    }
  }
  
  return (
    <div className={clsx(`rb:grid rb:grid-cols-${block ? 1 : options.length}`, {
      'rb:gap-3': !block,
      'rb:gap-4': block,
    })}>
      {/* Render each option as a selectable card */}
      {options.map(option => (
        <div key={String(option.value)} className={clsx("rb:relative rb:border rb:rounded-lg rb:w-full rb:text-center rb:cursor-pointer", {
          'rb:border rb:border-[#171719]!': option.value === value,
          'rb:border-[#EBEBEB] rb:bg-white': option.value !== value,
          'rb:opacity-[0.75]': option.disabled,
          'rb:py-5 rb:px-3 rb:leading-5.5': !block,
          'rb:flex rb:items-center rb:text-left rb:gap-4 rb:py-3 rb:px-4 rb:leading-4': block,
        })} onClick={() => handleChange(option)}>
          {option.recommend && <div className="rb:absolute rb:right-0 rb:top-0 rb:bg-[#FF5D34] rb:rounded-[0px_7px_0px_8px] rb:text-[12px] rb:text-white rb:font-regular rb:leading-4 rb:py-1 rb:px-2">{t('common.recommend')}</div>}
          {/* Use custom render or default card layout */}
          {itemRender ? itemRender(option) : (
            <>
              {option.icon && <img src={option.icon} className={clsx("rb:size-10", {
                'rb:m-[0_auto] rb:mb-3': !block,
              })} />}
              <div>
                <div className="rb:font-medium rb:text-[#212332]">{option.label}</div>
                <div className="rb:mt-2 rb:text-[#5B6167] rb:text-[12px] rb:font-regular">{option.labelDesc}</div>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
};

export default RadioGroupCard;