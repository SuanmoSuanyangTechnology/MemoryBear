import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';

const ClusterNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {};
  const { icon, name, description } = data;

  return (
    <div className="rb:relative rb:h-full rb:w-full rb:p-3 rb-border rb:rounded-2xl rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)]">
      <Flex align="center" gap={8} className="rb:flex-1">
        <div className={`rb:size-6 rb:bg-cover ${icon}`} />
        <div className="rb:wrap-break-word rb:line-clamp-1 rb:flex-1 rb:text-[12px]">{name}</div>
      </Flex>
      {description && (
        <div className="rb:text-gray-600 rb:text-[11px] rb:leading-4 rb:mt-2 rb:line-clamp-2">{description}</div>
      )}
    </div>
  );
};

export default ClusterNode;
