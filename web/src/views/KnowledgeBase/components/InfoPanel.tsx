/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-18 16:27:41
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-11-19 19:59:36
 */
import { Divider } from 'antd';

export interface InfoItem {
  key: string;
  label: string;
  value: string | number | undefined;
  icon?: string;
}

interface InfoPanelProps {
  title: string;
  items: InfoItem[];
  className?: string;
}

const InfoPanel = ({ title, items, className = '' }: InfoPanelProps) => {
  return (
    <div className={`rb:w-full ${className}`}>
      <h2 className="rb:text-lg rb:font-medium">{title}</h2>
      <Divider />
      <div className='rb:flex rb:flex-col rb:items-start rb:gap-6'>
        {items.map((item) => (
          <div key={item.key} className='rb:flex rb:w-full rb:items-start rb:justify-start rb:gap-2'>
            {item.icon && <img src={item.icon} className='rb:size-4 rb:mt-[2px]' alt="" />}
            <div className='rb:flex rb:flex-col rb:text-left rb:gap-2'>
              <span className='rb:text-gray-500 rb:text-sm'>{item.label}</span>
              <span className='rb:text-gray-800'>{item.value ?? '-'}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default InfoPanel;
