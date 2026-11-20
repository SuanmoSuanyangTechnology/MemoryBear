/**
 * Application Record Modal
 * Displays submission records and review timeline for community market
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Flex, Timeline, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import { appExternalCatalog } from '@/api/application';
import type { ApplicationRecordModalRef, PublicationEvent, PublicationRecordData } from '../types';
import { formatDateTime } from '@/utils/format'
import Empty from '@/components/Empty'
import { formatVersion } from '@/utils/format';
import styles from './ApplicationRecordModal.module.css';

const DEFAULT_EVENT_COLOR = '#9ca3af';
const ERROR_EVENT_COLOR = '#ef4444';
const PROGRESSING_EVENT_COLOR = '#3b82f6';
const SUCCESS_EVENT_COLOR = '#22c55e';
const WARNING_EVENT_COLOR = '#f97316';

/**
 * Single source of truth for publication events: legend items, timeline copy and colors.
 * Copy is resolved from source + action (the API no longer returns `event_label`).
 * See 设计文档/接口文档/社区市场 · 申请与操作记录.md.
 */
const publicationEventConfigs: Array<{
  source: PublicationEvent['source'];
  action: PublicationEvent['event_type'];
  labelKey: string;
  color: string;
  showInLegend?: boolean;
}> = [
  // Only these five statuses are shown in the legend; all others use the withdrawn color
  { source: 'publisher', action: 'submitted', labelKey: 'application.recordEvent_submitted', color: PROGRESSING_EVENT_COLOR, showInLegend: true },
  { source: 'publisher', action: 'pending', labelKey: 'application.recordEvent_pending', color: WARNING_EVENT_COLOR, showInLegend: true },
  { source: 'platform', action: 'approved', labelKey: 'application.recordEvent_approved', color: SUCCESS_EVENT_COLOR, showInLegend: true },
  { source: 'platform', action: 'rejected', labelKey: 'application.recordEvent_rejected', color: ERROR_EVENT_COLOR, showInLegend: true },
  { source: 'publisher', action: 'withdrawn', labelKey: 'application.recordEvent_withdrawn', color: DEFAULT_EVENT_COLOR, showInLegend: true },
  { source: 'publisher', action: 'delisted', labelKey: 'application.recordEvent_publisher_delisted', color: DEFAULT_EVENT_COLOR },
  { source: 'publisher', action: 'relisted', labelKey: 'application.recordEvent_publisher_relisted', color: DEFAULT_EVENT_COLOR },
  { source: 'platform', action: 'delisted', labelKey: 'application.recordEvent_platform_delisted', color: DEFAULT_EVENT_COLOR },
  { source: 'platform', action: 'relisted', labelKey: 'application.recordEvent_platform_relisted', color: DEFAULT_EVENT_COLOR },
  { source: 'platform', action: 'listed', labelKey: 'application.recordEvent_platform_listed', color: DEFAULT_EVENT_COLOR },
  { source: 'platform', action: 'display_updated', labelKey: 'application.recordEvent_display_updated', color: DEFAULT_EVENT_COLOR },
];

const listingStatusLabelMap: Record<string, string> = {
  listed: 'application.marketStatus_listed',
  unlisted: 'application.marketStatus_unlisted',
};

/** Falls back to a same-action config (e.g. platform.pending uses the pending copy). */
const getEventConfig = (event: PublicationEvent) => {
  const action = event.action || event.event_type;
  return publicationEventConfigs.find(item => item.source === event.source && item.action === action)
    || publicationEventConfigs.find(item => item.action === action);
};

/**
 * Application Record Modal Component
 */
const ApplicationRecordModal = forwardRef<ApplicationRecordModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState<PublicationEvent[]>([]);
  const [recordData, setRecordData] = useState<PublicationRecordData | null>(null);

  /** Renders changed display fields as "名称、图标或简介". */
  const formatChangedFields = (fields?: PublicationEvent['changed_fields']) => {
    const labels = (fields || []).map(field =>
      t(`application.recordEvent_changedField_${field}`, { defaultValue: field })
    );
    if (labels.length < 2) return labels[0] || '';
    return labels.join('、');
  };

  const handleClose = () => {
    setVisible(false);
    setEvents([]);
    setRecordData(null);
  };

  const handleOpen = (app_id: string) => {
    setVisible(true);
    setLoading(true);
    appExternalCatalog({ resource_type: 'app', resource_id: app_id })
      .then((res) => {
        const data = res as PublicationRecordData;
        setEvents(data.events || []);
        setRecordData(data);
      })
      .catch(() => {
        setEvents([]);
        setRecordData(null);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  const handleViewMarketDetail = () => {
    navigate('/community-market');
    handleClose();
  };

  return (
    <RbModal
      title={t('application.applicationRecordTitle')}
      open={visible}
      onCancel={handleClose}
      footer={[
        <Button
          onClick={handleClose}
        >
          {t('application.close')}
        </Button>
      ]}
      width={600}
      confirmLoading={loading}
    >
      <Flex vertical gap={16}>
        {recordData && recordData.listing_status && recordData.current_version && (
          <div className="rb:p-4 rb:bg-gray-50 rb:rounded-lg rb:border rb:border-gray-200">
            <span className="rb:text-[13px] rb:text-gray-600">
              {t('application.marketStatus')}: {t(listingStatusLabelMap[recordData.listing_status] || 'application.marketStatus_unlisted')}
              {' · '}
              {t('application.shareVersion')}: {recordData.current_version || '-'}
            </span>
            {recordData.listing_status === 'listed' && (
              <a
                className="rb:ml-2 rb:text-[13px] rb:text-primary-600 rb:cursor-pointer rb:hover:text-primary-700"
                onClick={handleViewMarketDetail}
              >
                {t('application.viewMarketDetail')}
              </a>
            )}
          </div>
        )}
        <Flex wrap="wrap" gap={12} className="rb:mb-4! rb:pb-4! rb:border-b rb:border-gray-200">
          {publicationEventConfigs.filter(item => item.showInLegend).map((item) => (
            <Flex key={item.labelKey} align="center" gap={4}>
              <span
                className="rb:inline-block rb:size-2 rb:rounded-full"
                style={{ backgroundColor: item.color }}
              />
              <span className="rb:text-[13px]">{t(item.labelKey)}</span>
            </Flex>
          ))}
        </Flex>
        <Timeline
          mode="left"
          className={styles.timeline}
          items={events.map((event) => {
            const timeInfo = formatDateTime(event.occurred_at);
            const action = event.action || event.event_type;
            const eventConfig = getEventConfig(event);
            const changedFieldsText = action === 'display_updated' && event.changed_fields?.length
              ? formatChangedFields(event.changed_fields)
              : '';
            return {
              color: eventConfig?.color || DEFAULT_EVENT_COLOR,
              label: <div className="rb:text-gray-600 rb:wrap-break-word">{timeInfo}</div>,
              children: (
                <Flex vertical gap={4}>
                  <Flex align="center" gap={8}>
                    {event.version && <span className="rb:font-medium">{formatVersion(event.version || undefined)}</span>}
                    <span className="rb:text-[13px] rb:text-gray-600">
                      {t(eventConfig?.labelKey || `application.recordEvent_${action}`, { defaultValue: action })}
                    </span>
                  </Flex>
                  {changedFieldsText && (
                    <div className="rb:text-[12px] rb:text-gray-500">
                      {t('application.recordEvent_display_updated_detail', { fields: changedFieldsText })}
                    </div>
                  )}
                  {!changedFieldsText && event.reason && (
                    <div className="rb:text-[12px] rb:text-gray-500">
                      {event.reason}
                    </div>
                  )}
                </Flex>
              ),
            };
          })}
        />
        {events.length === 0 && !loading && (
          <Empty size={88} />
        )}
      </Flex>
    </RbModal>
  );
});

export default ApplicationRecordModal;
