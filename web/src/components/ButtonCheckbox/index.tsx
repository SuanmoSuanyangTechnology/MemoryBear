import { type FC, type ReactNode, useEffect } from 'react';
import { type RadioGroupProps } from 'antd';
import clsx from 'clsx'


interface ButtonCheckboxProps extends Omit<RadioGroupProps, 'onChange'> {
  checked?: boolean;
  onValueChange?: (checked: boolean) => void;
  onChange?: (checked: boolean) => void;
  icon?: string;
  checkedIcon?: string;
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
  // 监听value变化
  useEffect(() => {
    if (onValueChange) {
      onValueChange(checked);
    }
  }, [checked, onValueChange]);

  const handleChange = () => {
    if (onChange) {
      onChange(!checked);
    }
  }
  
  return (
    <div className={clsx("rb:flex rb:items-center rb:border rb:rounded-lg rb:px-2 rb:text-[12px] rb:h-6 rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
      "rb:bg-[rgba(21,94,239,0.06)] rb:border-[#155EEF] rb:text-[#155EEF]": checked,
      "rb:border-[#DFE4ED] rb:text-[#212332]": !checked,
    })} onClick={handleChange}>
      {icon && !checked && <img src={icon} className="rb:w-4 rb:h-4 rb:mr-1" />}
      {checkedIcon && checked && <img src={checkedIcon} className="rb:w-4 rb:h-4 rb:mr-1" />}
      {children}
    </div>
  );
};

export default ButtonCheckbox;