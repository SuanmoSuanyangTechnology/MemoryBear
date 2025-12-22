import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';

const GroupStartNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {}

  return (
    <div className={clsx('rb:group rb:relative rb:h-10 rb:w-20 rb:border rb:rounded-xl rb:flex rb:items-center rb:justify-center rb:text-[12px] rb:p-1 rb:box-border', {
      'rb:border-orange-500 rb:border-[3px] rb:bg-white rb:text-gray-700': data.isSelected,
      'rb:border-[#d1d5db] rb:bg-white rb:text-[#374151]': !data.isSelected
    })}>
      <span className="rb:overflow-hidden rb:whitespace-nowrap rb:text-ellipsis">
        {data.icon} {data.label}
      </span>
    </div>
  );
};

export default GroupStartNode;