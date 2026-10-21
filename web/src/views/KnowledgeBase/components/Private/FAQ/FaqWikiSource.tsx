import { useEffect, useRef } from 'react';
import { Col, Row, Typography } from 'antd';
import { EditorState } from '@codemirror/state';
import { MergeView } from '@codemirror/merge';
import { basicSetup } from 'codemirror';
import { EditorView, lineNumbers } from '@codemirror/view';
import { useTranslation } from 'react-i18next';

import { formatDateTime } from '@/utils/format';
import type { Snapshot } from './types';

const SourceContent = ({ content }: { content: string }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const view = new EditorView({
      parent: containerRef.current,
      state: EditorState.create({
        doc: content,
        extensions: [
          lineNumbers(),
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
          EditorView.lineWrapping,
          EditorView.theme({
            '&': { height: '320px', backgroundColor: '#fafbfc' },
            '.cm-scroller': { overflow: 'auto' },
            '.cm-content': { padding: '12px 0' },
            '.cm-gutters': { backgroundColor: '#f3f5f7', border: 'none' },
          }),
        ],
      }),
    });

    return () => view.destroy();
  }, [content]);

  return <div ref={containerRef} />;
};

const SourceDiff = ({ before, after }: { before: string; after: string }) => {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const extensions = [
      basicSetup,
      EditorState.phrases.of({
        '$ unchanged lines': t('knowledgeBase.faq.unchangedLines'),
      }),
      EditorView.lineWrapping,
      EditorView.theme({
        '&': { backgroundColor: '#fafbfc' },
        '.cm-content': { padding: '12px 0' },
        '.cm-gutters': { backgroundColor: '#f3f5f7', border: 'none' },
      }),
    ];
    const view = new MergeView({
      parent: containerRef.current,
      a: {
        doc: before,
        extensions: [
          ...extensions,
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
        ],
      },
      b: { doc: after, extensions },
      highlightChanges: true,
      gutter: true,
      collapseUnchanged: { minSize: 5 },
      diffConfig: { scanLimit: 10000 },
    });

    return () => view.destroy();
  }, [before, after, t]);

  return (
    <div
      ref={containerRef}
      className="rb:[&_.cm-mergeView]:h-80 rb:[&_.cm-mergeView]:overflow-auto"
    />
  );
};

const FaqWikiSource = ({ before, after }: { before?: Snapshot; after?: Snapshot }) => {
  const { t } = useTranslation();
  const snapshots = [
    { side: 'before', snapshot: before },
    { side: 'after', snapshot: before?.version === after?.version ? null : after },
  ].filter(({ snapshot }) => snapshot != null);

  if (before && after && snapshots.length === 2) {
    return (
      <div className="rb:overflow-hidden rb:rounded-lg rb:border rb:border-[#e5e7eb]">
        <Row className="rb:bg-[#f3f5f7]">
          {snapshots.map(({ side, snapshot }) => snapshot && (
            <Col span={12} key={side} className="rb:p-3">
              <Typography.Text strong>
                {t(`knowledgeBase.faq.${side}`)}
              </Typography.Text>
              <div className="rb:text-gray-600 rb:text-[12px]">
                v{snapshot.version} · {formatDateTime(snapshot.published_at_ms)}
              </div>
            </Col>
          ))}
        </Row>
        <SourceDiff before={before.content} after={after.content} />
      </div>
    );
  }

  return (
    <Row gutter={[16, 16]}>
      {snapshots.map(({ side, snapshot }) => snapshot && (
        <Col xs={24} md={snapshots.length === 2 ? 12 : 24} key={side}>
          <div className="rb:overflow-hidden rb:rounded-lg rb:border rb:border-[#e5e7eb]">
            <div className="rb:p-3 rb:bg-[#f3f5f7]">
              <Typography.Text strong>
                {t('knowledgeBase.faq.currentSource')}
              </Typography.Text>
              <div className="rb:text-gray-600 rb:text-[12px]">
                v{snapshot.version} · {formatDateTime(snapshot.published_at_ms)}
              </div>
            </div>
            <SourceContent content={snapshot.content} />
          </div>
        </Col>
      ))}
    </Row>
  );
};

export default FaqWikiSource;
