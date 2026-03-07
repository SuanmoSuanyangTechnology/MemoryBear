/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:29:41 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-03 19:02:43
 */
import { type FC, useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { Space, Input, Form, App, Flex } from 'antd';

import Tag, { type TagProps } from './components/Tag'
import RbCard from '@/components/RbCard/Card'
import { getReleaseList, rollbackRelease } from '@/api/application'
import ReleaseModal from './components/ReleaseModal'
import ReleaseShareModal from './components/ReleaseShareModal'
import type { Release, ReleaseModalRef, ReleaseShareModalRef } from './types'
import type { Application } from '@/views/ApplicationManagement/types'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format';
import Markdown from '@/components/Markdown'
import RbButton from '@/components/RbButton';
/**
 * Tag color mapping for release versions
 */
const tagColors: Record<Release['tagKey'], TagProps['color']> = {
  current: 'processing',
  rolledBack: 'warning',
  history: 'default',
}

const heightClass = 'rb:h-[calc(100vh-88px)]'
/**
 * Release page component
 * Manages application version releases, rollbacks, and version history
 * @param data - Application data
 * @param refresh - Function to refresh application data
 */
const ReleasePage: FC<{data: Application; refresh: () => void}> = ({data, refresh}) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const releaseModalRef = useRef<ReleaseModalRef>(null)
  const releaseShareModalRef = useRef<ReleaseShareModalRef>(null)
  const [selectedVersion, setSelectedVersion] = useState<Release | null>(null);
  const [releaseList, setReleaseList] = useState<Release[]>([])

  useEffect(() => {
    getData()
  }, [data.id])

  /**
   * Fetch release list data
   */
  const getData = () => {
    refresh()
    getReleaseList(data.id).then(res => {
      const response = res as Release[] || []
      setReleaseList(response)
      setSelectedVersion(response?.[0])
    })
  }
  /**
   * Rollback to selected version
   */
  const handleRollback = () => {
    if (!selectedVersion) return
    rollbackRelease(data.id, selectedVersion.version).then(() => {
      getData()
      message.success(t('common.operateSuccess'))
    })
  }
  return (
    <Flex gap={12}>
      <div className={`rb:overflow-y-auto rb:w-101 rb:flex-[0_0_auto] ${heightClass}`}>
        <Flex gap={12} vertical>
          <div className="rb:px-1">
            <div className="rb:text-[16px] rb:leading-5.5 rb:font-medium">{t('application.versionList')}</div>
            <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5">{t('application.versionListDesc')}</div>
          </div>
          {releaseList.length === 0
            ? <Empty />
            : selectedVersion && releaseList.map((version, index) => {
              const tagKey = version.id === data.current_release_id && index === 0
                ? 'current'
                : version.id === data.current_release_id
                ? 'rolledBack' :  'history'
              return (
                <RbCard 
                  key={version.version} 
                  title={<>
                    {version.version_name && version.version_name[0].toLocaleLowerCase() === 'v' ? version.version_name : version.version_name ? `v${version.version_name}` : `v${version.version}`}
                    {tagKey && <Tag color={tagColors[tagKey]} className="rb:ml-2">
                      {tagKey}
                    </Tag>}
                  </>}
                  className={clsx("rb:hover:shadow-[0px_2px_8px_0px_rgba(0,0,0,0.2)]! rb:cursor-pointer rb:bg-white", {
                    'rb:border-[#171719]!': version.id === selectedVersion.id,
                    'rb:border-[#DFE4ED] ': version.id !== selectedVersion.id
                  })}
                  headerType="borderless"
                  onClick={() => setSelectedVersion(version)}
                >
                  <div className="rb:leading-5 rb:line-clamp-2 rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap">
                    <Markdown content={version.release_notes} />
                  </div>
                  <div className="rb:mt-4 rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5">
                    {t('application.publishedOn')} {formatDateTime(version.published_at, 'YYYY-MM-DD HH:mm:ss')}
                  </div>
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5">
                    {t('application.publisher')}: {version.publisher_name}
                  </div>
                </RbCard>
              )
            })
          }
        </Flex>
      </div>
      <div className={`rb:overflow-y-auto rb:flex-[1_1_auto] ${heightClass}`}>
        <Form layout="vertical">
          <Flex align="center" className={clsx("rb:leading-6.5! rb:text-[18px] rb:font-medium rb:mb-4.75!", {
            'rb:justify-between': selectedVersion,
            'rb:justify-end': !selectedVersion
          })}>
            {selectedVersion && t('application.detailsOfVersion', { version: selectedVersion.version_name && selectedVersion.version_name[0].toLocaleLowerCase() === 'v' ? selectedVersion.version_name : selectedVersion.version_name ? `v${selectedVersion.version_name}` : `v${selectedVersion.version}` || '-' })}

            <Space size={10}>
              {selectedVersion && <>
                {/* <RbButton>{t('application.exportDSLFile')}</RbButton> */}
                {data.current_release_id !== selectedVersion.id && <RbButton onClick={handleRollback}>{t('application.willRollToThisVersion')}</RbButton>}
                <RbButton onClick={() => releaseShareModalRef.current?.handleOpen()}>{t('application.share')}</RbButton>
              </>}
              <RbButton type="primary" onClick={() => releaseModalRef.current?.handleOpen()}>{t('application.release')}</RbButton>
            </Space>
          </Flex>
          {selectedVersion && 
            <Flex gap={16} vertical>
              <RbCard
                title={t('application.VersionInformation')}
                headerType="borderless"
              >
                <div className="rb:grid rb:grid-cols-3 rb:gap-4">
                  <Form.Item label={t('application.releaseTime')} className="rb:mb-0!">
                    <Input value={formatDateTime(selectedVersion.published_at, 'YYYY-MM-DD HH:mm:ss')} disabled />
                  </Form.Item>
                  <Form.Item label={t('application.lastUpdateTime')} className="rb:mb-0!">
                    <Input value={formatDateTime(selectedVersion.updated_at, 'YYYY-MM-DD HH:mm:ss')} disabled />
                  </Form.Item>
                  <Form.Item label={t('application.editor')} className="rb:mb-0!">
                    <Input value={selectedVersion.publisher_name} disabled />
                  </Form.Item>
                </div>
              </RbCard>

              {/* Logs */}
              <RbCard
                title={t('application.changeLog')}
                headerType="borderless"
              >
                <Flex gap={16} vertical>
                {selectedVersion && (
                  <RbCard
                    headerType="borderless"
                    title={<div className="rb:text-[14px]">{formatDateTime(selectedVersion.published_at, 'YYYY-MM-DD HH:mm:ss')}</div>}
                    extra={<span className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4">{selectedVersion.publisher_name}</span>}
                    bodyClassName="rb:pt-0! rb:pb-3! rb:px-4!"
                  >
                    <div className="rb:font-regular rb:text-[#5B6167] rb:leading-4">
                      <Markdown content={selectedVersion.release_notes} />
                    </div>
                  </RbCard>
                )}
                </Flex>
              </RbCard>
            </Flex>
          }
        </Form>
      </div>
      <ReleaseModal
        data={data}
        ref={releaseModalRef}
        refreshTable={getData}
      />
      <ReleaseShareModal
        ref={releaseShareModalRef}
        version={selectedVersion}
      />
    </Flex>
  );
}
export default ReleasePage;