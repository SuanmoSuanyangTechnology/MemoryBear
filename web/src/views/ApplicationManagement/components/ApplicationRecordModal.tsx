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

const eventTypeColorMap: Record<string, string> = {
  submitted: '#3b82f6',
  pending: '#f97316',
  approved: '#22c55e',
  rejected: '#ef4444',
  withdrawn: '#9ca3af',
};

const listingStatusLabelMap: Record<string, string> = {
  listed: 'application.marketStatus_listed',
  unlisted: 'application.marketStatus_unlisted',
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
    navigate('/resources');
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
        {recordData && recordData.listing_status && (
          <div className="rb:p-4 rb:bg-gray-50 rb:rounded-lg rb:border rb:border-gray-200">
            <span className="rb:text-[13px] rb:text-gray-600">
              {t('application.marketStatus')}:
              {t(listingStatusLabelMap[recordData.listing_status] || 'application.marketStatus_unlisted')}
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
        <Flex gap={16} className="rb:mb-4! rb:pb-4! rb:border-b rb:border-gray-200">
          <Flex align="center" gap={4}>
            <span className="rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: eventTypeColorMap.submitted }} />
            <span className="rb:text-[13px]">{t('application.recordEvent_submitted')}</span>
          </Flex>
          <Flex align="center" gap={4}>
            <span className="rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: eventTypeColorMap.pending }} />
            <span className="rb:text-[13px]">{t('application.recordEvent_pending')}</span>
          </Flex>
          <Flex align="center" gap={4}>
            <span className="rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: eventTypeColorMap.approved }} />
            <span className="rb:text-[13px]">{t('application.recordEvent_approved')}</span>
          </Flex>
          <Flex align="center" gap={4}>
            <span className="rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: eventTypeColorMap.rejected }} />
            <span className="rb:text-[13px]">{t('application.recordEvent_rejected')}</span>
          </Flex>
          <Flex align="center" gap={4}>
            <span className="rb:inline-block rb:size-2 rb:rounded-full" style={{ backgroundColor: eventTypeColorMap.withdrawn }} />
            <span className="rb:text-[13px]">{t('application.recordEvent_withdrawn')}</span>
          </Flex>
        </Flex>
        <Timeline
          mode="left"
          items={events.map((event) => {
            const timeInfo = formatDateTime(event.occurred_at);
            return {
              color: eventTypeColorMap[event.event_type] || eventTypeColorMap.withdrawn,
              label: <div className="rb:text-gray-600 rb:wrap-break-word">{timeInfo}</div>,
              children: (
                <Flex vertical gap={4}>
                  <Flex align="center" gap={8}>
                    <span className="rb:font-medium">v{event.version}</span>
                    <span className="rb:text-[13px] rb:text-gray-600">
                      {event.event_label || t(`application.recordEvent_${event.event_type}`)}
                    </span>
                  </Flex>
                  {event.reason && (
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
          <div className="rb:text-center rb:text-gray-400 rb:py-8">
            {t('common.noData')}
          </div>
        )}
      </Flex>
    </RbModal>
  );
});

export default ApplicationRecordModal;
