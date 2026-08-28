/**
 * OnboardingGuide Component
 * Shown when no user-created configuration exists (is_system_default = false)
 * and walks the user through the 3-step onboarding flow:
 *   Create Config → Tune Engine Policies → Activate Online.
 */
import { type FC } from 'react'
import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import Tag from '@/components/Tag'

interface OnboardingGuideProps {
  /** Number of completed steps (0-3) */
  completed?: number;
  /** Callback fired when the "Create configuration" CTA is clicked */
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
    <div className="rb:rb-border rb:rounded-xl rb:bg-white rb:px-5! rb:py-3! rb:mb-4">
      <Flex align="center" justify="space-between" className="rb:mb-3!">
        <Flex align="center" gap={12}>
          <span className="rb:text-[16px] rb:font-medium rb:leading-5.5 rb:text-[#212332]">
            {t('memory.onboardingTitle')}
          </span>
          <Tag color="default" circle={true}>
            {t('memory.onboardingInProgress')}
          </Tag>
        </Flex>
        <span className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167]">{completed} / {steps.length}</span>
      </Flex>

      <div className="rb:grid rb:grid-cols-3 rb:gap-0">
        {steps.map((step, index) => {
          // Step 1 is actionable when no config exists; the remaining steps
          // unlock after a configuration has been created.
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
                  <Flex align="center" justify="center" className={clsx('rb:size-5 rb:rounded-full rb:text-[12px] rb:font-medium', {
                    'rb:bg-[#171719] rb:text-white': isActive,
                    'rb:bg-[#F0F0F0] rb:text-[#9A9A9A]': !isActive,
                  })}>
                    {index + 1}
                  </Flex>
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
