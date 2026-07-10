/**
 * OnboardingGuide Component
 * 新手引导：当不存在用户自建配置（is_system_default = false）时展示，
 * 引导用户完成「创建配置 -> 配置引擎策略 -> 设为线上生效」三步。
 */
import { type FC } from 'react'
import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

interface OnboardingGuideProps {
  /** 完成步骤数（0-3） */
  completed?: number;
  /** 点击「创建配置」回调 */
  onCreate: () => void;
}

const OnboardingGuide: FC<OnboardingGuideProps> = ({ completed = 0, onCreate }) => {
  const { t } = useTranslation()

  const steps = [
    {
      index: t('memory.onboardingStepLabel1'),
      title: t('memory.onboardingStep1Title'),
      desc: t('memory.onboardingStep1Desc'),
    },
    {
      index: t('memory.onboardingStepLabel2'),
      title: t('memory.onboardingStep2Title'),
      desc: t('memory.onboardingStep2Desc'),
    },
    {
      index: t('memory.onboardingStepLabel3'),
      title: t('memory.onboardingStep3Title'),
      desc: t('memory.onboardingStep3Desc'),
    },
  ]

  return (
    <div className="rb:rb-border rb:rounded-xl rb:bg-white rb:p-5! rb:mb-4">
      <Flex align="center" justify="space-between" className="rb:mb-4!">
        <Flex align="center" gap={12}>
          <span className="rb:text-[16px] rb:font-medium rb:leading-5.5 rb:text-[#212332]">
            {t('memory.onboardingTitle')}
          </span>
          <span className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167] rb:bg-[#F6F6F6] rb:rounded-full rb:px-2 rb:py-0.5">
            {t('memory.onboardingInProgress')}
          </span>
        </Flex>
        <span className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167]">{completed} / {steps.length}</span>
      </Flex>

      <div className="rb:grid rb:grid-cols-3 rb:gap-0">
        {steps.map((step, index) => {
          // 第一步在无配置时可操作，其余步骤待创建配置后开启
          const isActive = index === 0
          return (
            <Flex
              key={step.index}
              vertical
              justify="space-between"
              gap={12}
              className={clsx('rb:px-6! rb:py-1!', {
                'rb:border-l rb:border-[#EBEBEB]': index > 0,
                'rb:pl-0!': index === 0,
              })}
            >
              <div>
                <Flex align="center" gap={8} className="rb:mb-2!">
                  <span className={clsx('rb:size-5 rb:rounded-full rb:flex rb:items-center rb:justify-center rb:text-[12px] rb:font-medium', {
                    'rb:bg-[#171719] rb:text-white': isActive,
                    'rb:bg-[#F0F0F0] rb:text-[#9A9A9A]': !isActive,
                  })}>
                    {index + 1}
                  </span>
                  <span className="rb:text-[12px] rb:leading-4 rb:text-[#9A9A9A]">{step.index}</span>
                </Flex>
                <div className="rb:text-[14px] rb:font-medium rb:leading-5 rb:text-[#212332] rb:mb-1">{step.title}</div>
                <div className="rb:text-[12px] rb:leading-4.5 rb:text-[#5B6167]">{step.desc}</div>
              </div>

              {isActive
                ? <Flex
                    align="center"
                    justify="center"
                    className="rb:h-9 rb:rounded-lg rb:bg-[#171719] rb:text-white rb:text-[14px] rb:font-medium rb:leading-5 rb:cursor-pointer rb:hover:opacity-90"
                    onClick={onCreate}
                  >
                    {t('memory.createConfiguration')}
                  </Flex>
                : <Flex
                    align="center"
                    justify="center"
                    className="rb:h-9 rb:text-[12px] rb:leading-4.5 rb:text-[#9A9A9A]"
                  >
                    {t('memory.onboardingStepLocked')}
                  </Flex>
              }
            </Flex>
          )
        })}
      </div>
    </div>
  )
}

export default OnboardingGuide
