/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 17:30:11
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-16 12:07:14
 */
/**
 * Result Component
 * Displays real-time extraction results with progress tracking
 * Shows text preprocessing, knowledge extraction, node/edge creation, and deduplication
 */

import { type FC, useRef, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Space, Button, Progress, Flex } from 'antd'
import { ExclamationCircleFilled } from '@ant-design/icons'
import clsx from 'clsx'

import Card from './Card'
import RbAlert from '@/components/RbAlert'
import type { ResultProps } from './result/types'
import { useDebugChat } from './result/hooks/useDebugChat'
import { usePilotRun } from './result/hooks/usePilotRun'
import DebugChat from './result/DebugChat'
import ProcessData from './result/ProcessData'
import FinalResult from './result/FinalResult'

const Result: FC<ResultProps> = ({ loading, handleSave, disabled = false, pruningEnabled = true }) => {
  const { t } = useTranslation();
  const { id } = useParams()

  const abortRef = useRef<(() => void) | null>(null)

  /** Debug chat logic */
  const {
    msg,
    setMsg,
    chatList,
    chatLoading,
    fileList,
    setFileList,
    toolbarRef,
    streamLoadingRef,
    handleChatSend,
    clearChat,
  } = useDebugChat(id, abortRef)

  /** Pilot run logic */
  const {
    runLoading,
    activeTab,
    setActiveTab,
    testResult,
    coreEntitiesTab,
    setCoreEntitiesTab,
    textPreprocessing,
    chunking,
    knowledgeExtraction,
    creatingNodesEdges,
    deduplication,
    perceptual,
    ontologyCoverage,
    expandedCards,
    toggleCard,
    handleRun,
  } = usePilotRun(id, abortRef, chatList)

  useEffect(() => {
    return () => {
      abortRef.current?.()
      abortRef.current = null;
    }
  }, [])

  const modules = [
    ...(pruningEnabled ? [textPreprocessing] : []),
    chunking, knowledgeExtraction, creatingNodesEdges, perceptual, deduplication,
  ]
  const completedNum = modules.filter(item => item.status === 'completed').length

  return (
    <Card
      title={t('memoryExtractionEngine.exampleMemoryExtractionResults')}
      subTitle={t('memoryExtractionEngine.exampleMemoryExtractionResultsSubTitle')}
      headerClassName="rb:pb-0! rb:pt-4!"
      bodyClassName="rb:h-[calc(100%-50px)]! rb:overflow-y-auto rb:p-[16px_20px]!"
      extra={<Space size={8}>
        <Button
          disabled={disabled || runLoading || chatList.length === 0}
          onClick={clearChat}
        >{t('memoryExtractionEngine.clearChat')}</Button>
        <Button
          icon={<div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/common/save.svg')]"></div>}
          loading={loading}
          disabled={disabled}
          onClick={handleSave}
        >{t('common.save')}</Button>
        <Button
          type="primary"
          icon={<div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/memory/debug.svg')]"></div>}
          loading={runLoading}
          onClick={handleRun}
        >{t('memoryExtractionEngine.debug')}</Button>
      </Space>}
      className="rb:h-full!"
    >
      <DebugChat
        chatList={chatList}
        chatLoading={chatLoading}
        streamLoading={streamLoadingRef.current}
        message={msg}
        fileList={fileList}
        setFileList={setFileList}
        toolbarRef={toolbarRef}
        onChange={setMsg}
        onSend={handleChatSend}
      />

      {runLoading
        ? <>
          <RbAlert color="blue">
            <div className="rb:w-full">
              {t('memoryExtractionEngine.processing')}

              {/* Overall Progress */}
              <Flex gap={13} align="center">
                <Progress percent={completedNum * 100 / modules.length} showInfo={false} className="rb:flex-1!" />
                <div className="rb:text-[12px] rb:leading-4 rb:font-regular">
                  {t('memoryExtractionEngine.overallProgress')}{`${(completedNum*100/modules.length).toFixed(0)}%`}
                </div>
              </Flex>
            </div>
          </RbAlert>
        </>
        : !testResult || Object.keys(testResult).length === 0
        ? <RbAlert color="orange" icon={<ExclamationCircleFilled />}>
          {t('memoryExtractionEngine.warning')}
        </RbAlert>
        : <RbAlert color="green" icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/check_green.svg')]"></div>}>
          {t('memoryExtractionEngine.success')}
        </RbAlert>
      }

      <Space size={24} className="rb:mt-4! rb:mb-3!">
        {['processData', 'finalResult'].map(tab => (
          <div
            key={tab}
            className={clsx('rb:font-[MiSans-Bold] rb:font-bold rb:leading-5 rb:cursor-pointer', {
              'rb:text-[#212332]': activeTab === tab,
              'rb:text-[#A8A9AA]': activeTab !== tab,
            })}
            onClick={() => setActiveTab(tab)}
          >{t(`memoryExtractionEngine.${tab}`)}</div>
        ))}
      </Space>

      {activeTab === 'processData' &&
        <ProcessData
          textPreprocessing={textPreprocessing}
          chunking={chunking}
          knowledgeExtraction={knowledgeExtraction}
          creatingNodesEdges={creatingNodesEdges}
          deduplication={deduplication}
          perceptual={perceptual}
          pruningEnabled={pruningEnabled}
          expandedCards={expandedCards}
          toggleCard={toggleCard}
        />
      }

      {activeTab === 'finalResult' &&
        <FinalResult
          testResult={testResult}
          ontologyCoverage={ontologyCoverage}
          perceptual={perceptual}
          coreEntitiesTab={coreEntitiesTab}
          setCoreEntitiesTab={setCoreEntitiesTab}
          expandedCards={expandedCards}
          toggleCard={toggleCard}
        />
      }
    </Card>
  )
}
export default Result
