import { type FC } from 'react';
import { useTranslation } from 'react-i18next'
import { Card, Space } from 'antd'

import { nodeLibrary } from '../constant';

const NodeLibrary: FC = () => {
  const { t } = useTranslation()

  console.log('nodeLibrary', nodeLibrary)

  return (
    <div className="rb:w-80 rb:fixed rb:h-screen rb:left-0 rb:py-5 rb:px-5.5 rb:overflow-y-auto">
      <Space size={12} direction="vertical" className="rb:w-full">
        {nodeLibrary.map(category => (
          <Card
            key={category.category}
            type="inner"
            title={t(`workflow.${category.category}`)}
            classNames={{
              body: "rb:p-[10px]!",
              header: "rb:bg-[#F6F8FC]!"
            }}
          >
            <Space size={8} direction="vertical" className="rb:w-full">
              {category.nodes.map((node, nodeIndex) => (
                <div
                  key={nodeIndex}
                  className="rb:bg-white rb:rounded-lg rb:p-2 rb:border rb:border-[#DFE4ED] rb:cursor-pointer rb:flex rb:items-center rb:gap-2 rb:hover:border-[#155EEF] rb:hover:shadow-[0px_2px_4px_0px_rgba(33,35,50,0.15)]"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/reactflow', node.type);
                    e.dataTransfer.setData('application/json', JSON.stringify(node));
                  }}
                >
                  <img src={node.icon} className="rb:w-5 rb:h-5" />
                  <span className="rb:font-medium rb:text-[12px]">{t(`workflow.${node.type}`)}</span>
                </div>
              ))}
            </Space>
          </Card>
        ))}
      </Space>
    </div>
  );
};

export default NodeLibrary;