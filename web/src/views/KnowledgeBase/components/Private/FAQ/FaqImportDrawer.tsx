/**
 * Detail-page prototype import drawer: mode selection, file upload and a
 * failures-only preview. Validation success submits automatically, so there is
 * no confirm button; footer only shows the hint and Cancel.
 */
import { useState } from 'react';
import { Button, Dropdown, Flex, Space, Upload } from 'antd';
import clsx from 'clsx';

import RbDrawer from '@/components/RbDrawer';
import RadioGroupCard from '@/components/RadioGroupCard';
import FaqImportPreview from './FaqImportPreview';
import type { FaqImportStage } from './hooks/useFaqImport';
import useFaqImportI18n from './hooks/useFaqImportI18n';
import {
  downloadFaqImportTemplate,
  isFaqImportReady,
  type FaqImportParseResult,
  type FaqImportTemplateFormat,
} from './faqImportFile';
import { TEMPLATE_ICON_CLASS, UPLOAD_ICON_CLASS } from './faqImportIcons';
import type { FaqImportMode } from './types';

const { Dragger } = Upload;

interface FaqImportDrawerProps {
  open: boolean;
  mode: FaqImportMode;
  onModeChange: (mode: FaqImportMode) => void;
  fileName: string;
  readId: number;
  parsing: boolean;
  stage: FaqImportStage;
  parseResult: FaqImportParseResult | null;
  onFile: (file: File) => void;
  onConfirm: () => void;
  onClose: () => void;
}

export default function FaqImportDrawer({
  open, mode, onModeChange, fileName, readId, parsing, stage, parseResult, onFile, onConfirm, onClose,
}: FaqImportDrawerProps) {
  const key = useFaqImportI18n();
  const [dragging, setDragging] = useState(false);
  const busy = parsing || stage === 'submitting' || stage === 'running';

  const hasFailures = Boolean(parseResult && !parseResult.error
    && parseResult.rows.some((row) => row.errors.length > 0));
  const footerHint = parsing
    ? key('footerParsing')
    : !parseResult
      ? key('footerUploadFirst')
      : hasFailures || parseResult.error
        ? key('footerInvalid')
        : key('footerReady');

  const ready = Boolean(parseResult && isFaqImportReady(parseResult) && stage === 'input' && !parsing);
  const count = parseResult?.rows.length ?? 0;
  const confirmLabel = key('confirmButton', { count });

  return (
    <RbDrawer
      title={key('title')}
      open={open}
      width={560}
      onClose={onClose}
      footer={(
        <Flex justify="space-between" align="center" gap={12} wrap className="rb:w-full">
          <span className={clsx('rb:text-[12px]', (hasFailures || parseResult?.error) ? 'rb:text-red-500' : 'rb:text-gray-600')}>
            {footerHint}
          </span>
          <Space size={12}>
            <Button onClick={onClose}>{key('cancel')}</Button>
            {ready &&
              <Button type="primary" danger={mode === 'replace'} onClick={onConfirm}>
                {confirmLabel}
              </Button>
            }
          </Space>
        </Flex>
      )}
    >
      <Flex vertical className="rb:h-full rb:overflow-y-auto rb:pr-1!">
        <Flex vertical gap={4}>
          <Flex vertical gap={6} className="rb:mb-3!">
            <span className="rb:font-medium rb:text-gray-800">
              {key('modeLabel')} <span className="rb:text-red-500">*</span>
            </span>
            <RadioGroupCard
              allowClear={false}
              value={mode}
              onChange={(value) => onModeChange((value ?? 'append') as FaqImportMode)}
              disabled={busy}
              className="rb:min-w-0"
              options={[
                { value: 'append', label: key('append'), labelDesc: key('appendDesc') },
                { value: 'replace', label: key('replace'), labelDesc: key('replaceDesc') },
              ]}
            />
          </Flex>
          <Flex justify="space-between" align="center" gap={12} wrap className="rb:mt-2!">
            <span className="rb:font-medium rb:text-gray-800">
              {key('uploadLabel')} <span className="rb:text-red-500">*</span>
            </span>
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  { key: 'csv', label: key('templateCsv') },
                  { key: 'xlsx', label: key('templateXlsx') },
                  { key: 'json', label: key('templateJson') },
                ],
                onClick: ({ key: format }) => downloadFaqImportTemplate(format as FaqImportTemplateFormat),
              }}
            >
              <Button className="rb:group/export" icon={<div className={TEMPLATE_ICON_CLASS} />} disabled={busy}>{key('template')}</Button>
            </Dropdown>
          </Flex>
          <div
            onDragOver={(event) => {
              event.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={() => setDragging(false)}
            className={clsx(
              'rb:mt-2! rb:border rb:border-dashed rb:rounded-xl rb:px-2! rb:py-2! rb:transition-colors rb:hover:border-primary-600',
              dragging
                ? 'rb:border-primary-600 rb:bg-blue-50'
                : parseResult && !parseResult.error && !hasFailures
                  ? 'rb:border-green-600 rb:bg-green-50'
                  : parseResult
                    ? 'rb:border-red-300 rb:bg-red-50'
                    : 'rb:border-gray-300 rb:bg-white',
            )}
          >
            <Dragger
              accept=".csv,.xlsx,.json"
              multiple={false}
              showUploadList={false}
              disabled={busy}
              className="rb:block!"
              style={{ border: 'none', background: 'transparent', height: 'auto' }}
              beforeUpload={(file) => {
                onFile(file);
                return Upload.LIST_IGNORE;
              }}
              height={130}
            >
              <Flex vertical align="center" gap={4}>
                <div className={UPLOAD_ICON_CLASS} />
                <strong className="rb:text-gray-800">{fileName || key('uploadIdleTitle')}</strong>
                <span className="rb:text-[12px] rb:text-gray-600">
                  {parsing ? key('uploadingMeta') : fileName ? key('uploadLoadedMeta') : key('uploadIdleMeta')}
                </span>
              </Flex>
            </Dragger>
          </div>
          <div className="rb:mt-2! rb:text-[12px] rb:text-gray-600">
            <div>{key('help')}</div>
            <div>{key('helpHeaders')}</div>
            <div>{key('helpAutoSubmit')}</div>
          </div>
          {parseResult && <FaqImportPreview key={readId} result={parseResult} />}
        </Flex>
      </Flex>
    </RbDrawer>
  );
}
