/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:31:30 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-30 11:55:10
 */
import { Flex } from 'antd';
import type { ReactShapeConfig } from '@antv/x6-react-shape';

const AddNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {};

  return (
    <Flex
      align="center"
      justify="center"
      gap={4}
      className="rb:text-[#212332] rb:font-medium rb:text-[12px] rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:border rb:rounded-lg rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:border-[#FCFCFD] rb:flex rb:items-center rb:justify-center"
    >
      <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/workflow/node_plus.png')]"></div>
      {data.label}
    </Flex>
  );
};

export default AddNode;