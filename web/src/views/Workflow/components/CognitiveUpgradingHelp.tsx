import React, { useMemo } from 'react';
import { Flex, Popover, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import { openHelpCenter } from '@/utils/help';

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
    animation: shake 1.4s ease-in-out infinite;
  }
  @keyframes dotMove {
    0% {
      left: 4%;
      opacity: 0;
    }
    5% {
      opacity: 1;
    }
    18% {
      left: 22%;
    }
    34% {
      left: 44%;
    }
    50% {
      left: 54%;
    }
    66% {
      left: 72%;
    }
    82% {
      left: 90%;
    }
    92% {
      left: 96%;
      opacity: 1;
    }
    100% {
      left: 96%;
      opacity: 0;
    }
  }
  .dot-move-animation {
    animation: dotMove 3s ease-in-out infinite;
  }
  @keyframes nodeHighlight1 {
    0%, 10%, 24%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    10%, 20% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  @keyframes nodeHighlight2 {
    0%, 24%, 40%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    26%, 36% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  @keyframes nodeHighlight3 {
    0%, 42%, 56%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    46%, 52% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  @keyframes nodeHighlight4 {
    0%, 58%, 72%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    62%, 68% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  @keyframes nodeHighlight5 {
    0%, 74%, 88%, 100% {
      opacity: 0.55;
      transform: translateY(0);
    }
    78%, 84% {
      opacity: 1;
      transform: translateY(-2px);
    }
  }
  .node-highlight-1 {
    animation: nodeHighlight1 3s ease-in-out infinite;
  }
  .node-highlight-2 {
    animation: nodeHighlight2 3s ease-in-out infinite;
  }
  .node-highlight-3 {
    animation: nodeHighlight3 3s ease-in-out infinite;
  }
  .node-highlight-4 {
    animation: nodeHighlight4 3s ease-in-out infinite;
  }
  .node-highlight-5 {
    animation: nodeHighlight5 3s ease-in-out infinite;
  }
  .node-hover-highlight:hover {
    opacity: 1 !important;
    transform: translateY(-2px) !important;
  }
  @keyframes questionHalo {
    0% {
      box-shadow: 0 0 0 0 rgba(21, 94, 239, 0.25);
    }

    70% {
      box-shadow: 0 0 0 7px rgba(21, 94, 239, 0);
    }
    100% {
      box-shadow: 0 0 0 0 rgba(21, 94, 239, 0);
    }
  }
  .question-halo {
    animation: questionHalo 1.8s ease-in-out infinite;
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
    type: 'end',
    icon: 'rb:bg-[url("@/assets/images/workflow/end.svg")]',
    labelKey: 'workflow.end',
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
  const { t, i18n } = useTranslation();

  const gotoHelpCenter = () => {
    const currentLang = i18n.language;
    const lang = currentLang === 'zh' ? 'zh' : 'en';
    openHelpCenter(lang, 'memory-read');
  };

  const cognitiveUpgradingPopover = useMemo(() => (
    <div className="rb:w-100 rb:text-[12px]">
      <Flex align="center" justify="space-between">
        <div className="rb:font-medium rb:mb-3 rb:text-[14px]">{t('workflow.cognitiveUpgrading.usageHelp')}</div>
        <Button
          type="link"
          size="small"
          icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>}
          onClick={gotoHelpCenter}
        >
          {t('workflow.cognitiveUpgrading.tutorial')}
        </Button>
      </Flex>
      <div className="rb:bg-[#F9F9F9] rb:rounded-xl rb:py-3 rb:px-3.5">
        <div className="rb:text-center rb:mb-3">{t('workflow.cognitiveUpgrading.recommendedUsage')}</div>
        <div className="rb:relative rb:grid rb:grid-cols-9 rb:gap-1">
          <span className="rb:absolute rb:top-3 rb:size-2 rb:rounded-full rb:bg-[#155EEF] dot-move-animation"></span>
          {cognitiveUpgradingWorkflowNodes.map((node, index) => (
            <React.Fragment key={node.type}>
              <Flex 
                vertical gap={4} align="center"
                className={`rb:text-[center] rb:transition-[all_.3s] node-highlight-${index + 1} node-hover-highlight`}
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
          <div className="rb:relative rb:size-4 rb:shrink-0">
            <div className="rb:relative rb:rounded-full question-halo rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:z-10"></div>
          </div>
        </Popover>
        <span 
          className="rb:bg-[#155EEF] rb:text-[#FFFFFF] rb:text-[10px] rb:px-1.5 rb:py-0.75 rb:rounded-md! rb:inline-block shake-animation"
        >{t('workflow.cognitiveUpgrading.seeUsage')}</span>
      </>
    </>
  );
};

export default CognitiveUpgradingHelp;
