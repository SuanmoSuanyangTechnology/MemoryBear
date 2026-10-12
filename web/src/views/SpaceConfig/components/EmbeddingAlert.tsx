import { type FC, useState, useRef, useEffect, useCallback, type Dispatch, type SetStateAction } from 'react'
import { Button, Flex, Space, Progress } from 'antd';
import { useTranslation } from 'react-i18next';

import {
  getWorkspaceReembedCurrent,
  getWorkspaceReembedCurrenEndUsersRetryFailed
} from '@/api/workspaces';
import RbAlert, { type RbAlertProps } from '@/components/RbAlert'
import EmbeddingRebuildModal, { type EmbeddingRebuildModalRef } from './EmbeddingRebuildModal'
import EmbeddingRebuildDetailModal, { type EmbeddingRebuildDetailModalRef } from './EmbeddingRebuildDetailModal'


export type Status = 'pending' | 'failed' | 'running' | 'queued' | 'succeeded';
export interface ReembedInfo {
  id: string;
  workspace_id: string;
  status: Status;
  old_model_name: string;
  new_model_name: string;
  total_end_users: number;
  processed_end_users: number;
  failed_nodes: number;
  end_users: Record<Status, number>;
  error: null;
  created_at: number;
  started_at: number;
  finished_at: number | null;
}

interface EmbeddingAlertProps {
  reembedJobId?: string | null;
  source?: 'space' | 'user';
  onChange?: Dispatch<SetStateAction<string | null | undefined>>;
  className?: string;
}

interface SpaceAlertConfig {
  titleKey: string;
  descriptionKey: string;
  showProgress?: boolean;
}

const alertColors: Record<Status, RbAlertProps['color']> = {
  succeeded: 'green',
  pending: 'orange',
  queued: 'orange',
  running: 'orange',
  failed: 'red',
}

const spaceAlertConfigs: Partial<Record<Status, SpaceAlertConfig>> = {
  running: {
    titleKey: 'space.reembedding.alert.runningTitle',
    descriptionKey: 'space.reembedding.alert.runningDescription',
    showProgress: true,
  },
  succeeded: {
    titleKey: 'space.reembedding.alert.succeededTitle',
    descriptionKey: 'space.reembedding.alert.succeededDescription',
  },
  failed: {
    titleKey: 'space.reembedding.alert.failedTitle',
    descriptionKey: 'space.reembedding.alert.failedDescription',
  },
}

const getUserAlertKey = (reembedInfo: ReembedInfo) => {
  const { status, end_users: counts } = reembedInfo

  if (status === 'queued' || status === 'pending') {
    return 'space.reembedding.alert.userQueued'
  }
  if (status === 'running') {
    if (counts.failed > 0) {
      return 'space.reembedding.alert.userRunningFailed'
    }
    if (counts.running > 0 && counts.queued > 0) {
      return 'space.reembedding.alert.userRunningQueued'
    }
    if (counts.succeeded > 0 && counts.queued > 0) {
      return 'space.reembedding.alert.userSucceededQueued'
    }
    return null
  }
  if (status === 'succeeded') {
    return 'space.reembedding.alert.userSucceeded'
  }
  if (status === 'failed') {
    return 'space.reembedding.alert.userFailed'
  }
  return null
}

const EmbeddingAlert: FC<EmbeddingAlertProps> = ({
  reembedJobId,
  source = 'space',
  onChange,
  className,
}) => {
  const { t } = useTranslation();
  const embeddingRebuildModalRef = useRef<EmbeddingRebuildModalRef>(null)
  const embeddingRebuildDetailModalRef = useRef<EmbeddingRebuildDetailModalRef>(null)
  const intervalIdRef = useRef<number | null>(null)
  const hasCheckedInitialModalRef = useRef(false)

  const [reembedInfo, setReembedInfo] = useState<ReembedInfo>({} as ReembedInfo)
  const handleViewDetail = () => {
    embeddingRebuildDetailModalRef.current?.handleOpen()
  }

  const getCurrentReembed = useCallback(() => {
    getWorkspaceReembedCurrent()
      .then(res => {
        const response = res as ReembedInfo;
        if (response) {
          setReembedInfo(response)
          onChange?.(response.status !== 'succeeded' ? response.id : null)
        }
      })
  }, [onChange])

  const handleRetryFailed = () => {
    getWorkspaceReembedCurrenEndUsersRetryFailed()
      .then(res => {
        if (res) {
          getCurrentReembed()
        }
      })
  }
  const stopPolling = () => {
    if (intervalIdRef.current !== null) {
      clearInterval(intervalIdRef.current)
      intervalIdRef.current = null
    }
  }

  useEffect(() => {
    stopPolling()
    getCurrentReembed()
  }, [reembedJobId, getCurrentReembed])
  useEffect(() => {
    stopPolling()
    if (reembedInfo.status === 'running' || reembedInfo.status === 'pending') {
      intervalIdRef.current = setInterval(() => {
        getCurrentReembed()
      }, 3000)
    }

    return stopPolling
  }, [reembedInfo.status, getCurrentReembed])
  useEffect(() => {
    if (source !== 'user' || !reembedInfo.status || hasCheckedInitialModalRef.current) {
      return
    }

    hasCheckedInitialModalRef.current = true
    if (reembedInfo.status !== 'succeeded') {
      embeddingRebuildModalRef.current?.handleOpen()
    }
  }, [source, reembedInfo.status])

  if (!reembedInfo.status) {
    return null
  }

  const spaceAlertConfig = spaceAlertConfigs[reembedInfo.status]
  const userAlertKey = getUserAlertKey(reembedInfo)
  const counts = reembedInfo.end_users

  return (
    <>
      <RbAlert color={alertColors[reembedInfo.status]} className={className}>
        <Flex align="center" justify="space-between" gap={12} className="rb:w-full!">
          {source === 'space' && spaceAlertConfig && (
            <div className="rb:flex-1!">
              <div className="rb:font-semibold rb:mb-2">{t(spaceAlertConfig.titleKey)}</div>
              <div>{t(spaceAlertConfig.descriptionKey, { model: reembedInfo.new_model_name })}</div>
              {spaceAlertConfig.showProgress && (
                <Progress percent={reembedInfo.processed_end_users / reembedInfo.total_end_users * 100} showInfo={false} />
              )}
            </div>
          )}
          {source === 'user' && userAlertKey && (
            <div className="rb:flex-1!">
              {t(userAlertKey, {
                queued: counts.queued || 0,
                running: counts.running || 0,
                succeeded: counts.succeeded || 0,
                failed: counts.failed || 0,
              })}
            </div>
          )}
          <Space size={12} className="rb:shrink-0!">
            {reembedInfo.status === 'failed' && (
              <Button type="primary" onClick={handleRetryFailed}>
                {t('space.reembedding.retryFailedTasks')}
              </Button>
            )}
            <Button onClick={() => embeddingRebuildModalRef.current?.handleOpen()}>
              {t('space.reembedding.viewDetails')}
            </Button>
          </Space>
        </Flex>
      </RbAlert>
      <EmbeddingRebuildModal
        reembedInfo={reembedInfo}
        ref={embeddingRebuildModalRef}
        handleViewDetail={handleViewDetail}
        refresh={getCurrentReembed}
      />
      <EmbeddingRebuildDetailModal ref={embeddingRebuildDetailModalRef} refresh={getCurrentReembed} />
    </>
  )
}

export default EmbeddingAlert
