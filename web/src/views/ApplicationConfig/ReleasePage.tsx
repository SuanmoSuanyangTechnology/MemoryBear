import { type FC, useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { Button, Space, Input, Form, App } from 'antd';
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
const tagColors: Record<Release['tagKey'], TagProps['color']> = {
  current: 'processing',
  rolledBack: 'warning',
  history: 'default',
}

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

  const getData = () => {
    refresh()
    getReleaseList(data.id).then(res => {
      const response = res as Release[] || []
      setReleaseList(response)
      setSelectedVersion(response?.[0])
    })
  }
  const handleRollback = () => {
    if (!selectedVersion) return
    rollbackRelease(data.id, selectedVersion.version).then(() => {
      getData()
      message.success(t('common.operateSuccess'))
    })
  }
  return (
    <div className="rb:flex rb:h-[calc(100vh-64px)]">
      <div className="rb:h-full rb:overflow-y-auto rb:w-108 rb:flex-[0_0_auto] rb:border-r rb:border-[#DFE4ED] rb:p-4">
        <Space size={16} direction="vertical" style={{ width: '100%' }}>
          <div className="rb:leading-5.5 rb:px-1">
            {t('application.versionList')}
            <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1 rb:leading-4">{t('application.versionListDesc')}</div>
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
                  className={clsx("rb:hover:border-[#155EEF]! rb:cursor-pointer", {
                    'rb:bg-[rgba(21,94,239,0.06)]! rb:border-[#155EEF]!': version.id === selectedVersion.id,
                    'rb:border-[#DFE4ED] rb:bg-[#FBFDFF]': version.id !== selectedVersion.id
                  })}
                  headerType="borderless"
                  onClick={() => setSelectedVersion(version)}
                >
                  <div className="rb:leading-5 rb:line-clamp-2 rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap">
                    <Markdown content={version.release_notes} />
                  </div>
                  <div className="rb:mt-4 rb:text-[12px] rb:text-[#5B6167] rb:leading-4">
                    {t('application.publishedOn')} {formatDateTime(version.published_at, 'YYYY-MM-DD HH:mm:ss')}
                  </div>
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1 rb:leading-4">
                    {t('application.publisher')}: {version.publisher_name}
                  </div>
                </RbCard>
              )
            })
          }
        </Space>
      </div>
      <div className="rb:h-full rb:overflow-y-auto rb:flex-[1_1_auto] rb:p-4">
        <Form layout="vertical">
          <div className={clsx("rb:leading-5.5 rb:px-1 rb:flex rb:items-center rb:text-[16px] rb:font-medium rb:mb-5.25", {
            'rb:justify-between': selectedVersion,
            'rb:justify-end': !selectedVersion
          })}>
            {selectedVersion && t('application.DetailsOfVersion', { version: selectedVersion.version_name && selectedVersion.version_name[0].toLocaleLowerCase() === 'v' ? selectedVersion.version_name : selectedVersion.version_name ? `v${selectedVersion.version_name}` : `v${selectedVersion.version}` || '-' })}

            <Space size={10}>
              {selectedVersion && <>
                {/* <Button>{t('application.exportDSLFile')}</Button> */}
                {data.current_release_id !== selectedVersion.id && <Button onClick={handleRollback}>{t('application.willRollToThisVersion')}</Button>}
                <Button type="primary" ghost onClick={() => releaseShareModalRef.current?.handleOpen()}>{t('application.share')}</Button>
              </>}
              <Button type="primary" onClick={() => releaseModalRef.current?.handleOpen()}>{t('application.release')}</Button>
            </Space>
          </div>
          {selectedVersion && 
            <Space size={16} direction="vertical" style={{ width: '100%' }}>
              <RbCard title={t('application.VersionInformation')} headerType="borderless">
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

              {/* 日志 */}
              <RbCard title={t('application.changeLog')} headerType="borderless">
                <Space size={16} direction="vertical" style={{ width: '100%' }}>
                {selectedVersion && (
                  <RbCard
                    headerType="borderBL"
                    title={<div className="rb:text-[14px]">{formatDateTime(selectedVersion.published_at, 'YYYY-MM-DD HH:mm:ss')}</div>}
                    extra={<span className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4">{selectedVersion.publisher_name}</span>}
                  >
                    <div className="rb:font-medium rb:font-regular rb:text-[12px] rb:text-[#5B6167] rb:leading-4">
                      <Markdown content={selectedVersion.release_notes} />
                    </div>
                  </RbCard>
                )}
                </Space>
              </RbCard>
            </Space>
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
    </div>
  );
}
export default ReleasePage;