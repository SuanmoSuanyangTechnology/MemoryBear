import React, { useMemo } from 'react';
import { Flex, Popover } from 'antd';
import { useTranslation } from 'react-i18next';

const shakeAnimation = `
  @keyframes shake {
    0%, 100% {
      transform: translateX(0);
    }
    50% {
      transform: translateX(3px);
    }
  }
  .shake-animation {
    animation: shake 4.4s ease-in-out infinite;
  }
  @keyframes dotMove {
    0% {
      left: 6%;
      opacity: 0;
    }
    6% {
      opacity: 1;
    }
    28% {
      left: 33%;
    }
    52% {
      left: 60%;
    }
    78% {
      left: 90%;
    }
    92% {
      left: 92%;
      opacity: 1;
    }
    100% {
      left: 92%;
      opacity: 0;
    }
  }
  .dot-move-animation {
    animation: dotMove 2.5s ease-in-out infinite;
  }
  @keyframes nodeHighlight1 {
    0%, 4%, 18%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    8%, 14% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  @keyframes nodeHighlight2 {
    0%, 26%, 40%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    30%, 36% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  @keyframes nodeHighlight3 {
    0%, 48%, 62%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    52%, 58% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  @keyframes nodeHighlight4 {
    0%, 74%, 88%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    82%, 84% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  .node-highlight-1 {
    animation: nodeHighlight1 2.5s ease-in-out infinite;
  }
  .node-highlight-2 {
    animation: nodeHighlight2 2.5s ease-in-out infinite;
  }
  .node-highlight-3 {
    animation: nodeHighlight3 2.5s ease-in-out infinite;
  }
  .node-highlight-4 {
    animation: nodeHighlight4 2.5s ease-in-out infinite;
  }
`;
export interface CognitiveUpgradingNode {
  type: string;
  icon: string;
  labelKey: string;
}

interface CognitiveUpgradingNodeDesc {
  type: string;
  icon: string;
  labelKey: string;
  descKey: string;
}

const cognitiveUpgradingWorkflowNodes: CognitiveUpgradingNode[] = [
  {
    type: 'start',
    icon: 'rb:bg-[url("@/assets/images/workflow/start.svg")]',
    labelKey: 'workflow.start',
  },
  {
    type: 'memory-read',
    icon: 'rb:bg-[url("@/assets/images/workflow/memory-read.svg")]',
    labelKey: 'workflow.memory-read',
  },
  {
    type: 'llm',
    icon: 'rb:bg-[url("@/assets/images/workflow/llm.svg")]',
    labelKey: 'workflow.llm',
  },
  {
    type: 'memory-write',
    icon: 'rb:bg-[url("@/assets/images/workflow/memory-write.svg")]',
    labelKey: 'workflow.memory-write',
  },
];

const cognitiveUpgradingNodeDescriptions: CognitiveUpgradingNodeDesc[] = [
  {
    type: 'memory-read',
    icon: 'rb:bg-[url("@/assets/images/workflow/memory-read.svg")]',
    labelKey: 'workflow.memory-read',
    descKey: 'workflow.cognitiveUpgrading.memoryReadDesc',
  },
  {
    type: 'memory-write',
    icon: 'rb:bg-[url("@/assets/images/workflow/memory-write.svg")]',
    labelKey: 'workflow.memory-write',
    descKey: 'workflow.cognitiveUpgrading.memoryWriteDesc',
  },
];

const CognitiveUpgradingHelp = () => {
  const { t } = useTranslation();

  const cognitiveUpgradingPopover = useMemo(() => (
    <div className="rb:w-100 rb:text-[12px]">
      <div className="rb:font-medium rb:mb-3 rb:text-[14px]">{t('workflow.cognitiveUpgrading.usageHelp')}</div>
      <div className="rb:bg-[#F9F9F9] rb:rounded-xl rb:py-3 rb:px-3.5">
        <div className="rb:text-center rb:mb-3">{t('workflow.cognitiveUpgrading.recommendedUsage')}</div>
        <div className="rb:relative rb:grid rb:grid-cols-7 rb:gap-1">
          <span className="rb:absolute rb:top-3 rb:size-2 rb:rounded-full rb:bg-[#155EEF] dot-move-animation"></span>
          {cognitiveUpgradingWorkflowNodes.map((node, index) => (
            <React.Fragment key={node.type}>
              <Flex 
                vertical gap={4} align="center"
                className={`rb:text-[center] rb:transition-[opacity_.3s] node-highlight-${index + 1}`}
              >
                <div className={`rb:size-6.5 rb:bg-cover ${node.icon}`}></div>
                <span className="rb:text-[10px] rb:text-center">{t(node.labelKey)}</span>
              </Flex>
              {index < cognitiveUpgradingWorkflowNodes.length - 1 && (
                <div className="rb:mt-3 rb:flex-1 rb:h-0.5 rb:bg-[#EBEBEB] rb:rounded-full"></div>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>

      {cognitiveUpgradingNodeDescriptions.map((node, index) => (
        <Flex key={node.type} gap={8} className={`${index === 0 ? ' rb:my-3!' : ''}`}>
          <div className={`rb:size-6.5 rb:bg-cover ${node.icon}`}></div>
          <div className="rb:flex-1">
            <div className="rb:font-medium">{t(node.labelKey)}</div>
            <div className="rb:mt-1">{t(node.descKey)}</div>
          </div>
        </Flex>
      ))}
    </div>
  ), [t]);

  return (
    <>
      <style>{shakeAnimation}</style>
      <>
        <Popover
          content={cognitiveUpgradingPopover}
          trigger="click"
          placement="right"
        >
          <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>
        </Popover>
        <span 
          className="rb:bg-[#155EEF] rb:text-[#FFFFFF] rb:text-[10px] rb:px-1.5 rb:py-0.75 rb:rounded-md! rb:inline-block shake-animation"
        >{t('workflow.cognitiveUpgrading.seeUsage')}</span>
      </>
    </>
  );
};

export default CognitiveUpgradingHelp;
