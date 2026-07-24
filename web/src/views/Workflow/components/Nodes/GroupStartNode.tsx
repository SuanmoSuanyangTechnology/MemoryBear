import { Flex } from 'antd';
import type { ReactShapeConfig } from '@antv/x6-react-shape';

const GroupStartNode: ReactShapeConfig['component'] = () => {
  return (
    <Flex align="center" justify="center" className='rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:border rb:rounded-xl rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:border-[#FCFCFD]'>
      <div className="rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/workflow/start.svg')]" />
    </Flex>
  );
};

export default GroupStartNode;