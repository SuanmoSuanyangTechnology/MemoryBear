import { type FC, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
import { ListPlugin } from '@lexical/react/LexicalListPlugin';
import { LinkPlugin } from '@lexical/react/LexicalLinkPlugin';
import { ListNode, ListItemNode } from '@lexical/list';
import { LinkNode } from '@lexical/link';
import { OnChangePlugin } from '@lexical/react/LexicalOnChangePlugin';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { useEffect, useRef } from 'react';
import NoteFormatPlugin from './NoteFormatPlugin';
import type { FormatState } from './NoteFormatPlugin';
import { LinkPopover, EditLinkPopover } from './NoteLinkPopovers';

const theme = {
  paragraph: 'editor-paragraph',
  text: {
    bold: 'editor-text-bold',
    italic: 'editor-text-italic',
    strikethrough: 'note-text-strikethrough',
  },
  list: { ul: 'note-list-ul', listitem: 'note-list-item' },
  link: 'note-link',
};

const NOTE_NODES = [ListNode, ListItemNode, LinkNode];

const NOTE_STYLES = `
  .editor-text-bold { font-weight: bold; }
  .editor-text-italic { font-style: italic; }
  .note-text-strikethrough { text-decoration: line-through; }
  .note-list-ul { list-style-type: disc; padding-left: 1.2em; margin: 0; }
  .note-list-item { margin: 2px 0; }
  .note-link { color: #2563eb; text-decoration: underline; cursor: pointer; }
`;

const NoteInitPlugin: FC<{ value: string }> = ({ value }) => {
  const [editor] = useLexicalComposerContext();
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current || !value) return;
    initialized.current = true;
    try {
      const parsed = JSON.parse(value);
      if (parsed?.root) {
        const state = editor.parseEditorState(JSON.stringify(parsed));
        editor.setEditorState(state);
        return;
      }
    } catch {}
  }, [editor, value]);
  return null;
};


interface NoteEditorProps {
  nodeId: string;
  value: string;
  fontSize?: number;
  onChange: (val: string) => void;
  onFormatChange?: (state: FormatState) => void;
}

const NoteEditor: FC<NoteEditorProps> = ({ nodeId, value, fontSize = 12, onChange, onFormatChange }) => {
  const { t } = useTranslation();
  const [linkState, setLinkState] = useState<{ url: string; rect: DOMRect } | null>(null);
  const [editLinkRect, setEditLinkRect] = useState<{ url: string; rect: DOMRect } | null>(null);
  const removingLink = useRef(false);

  useEffect(() => {
    if (!linkState) return;
    const handler = () => setLinkState(null);
    window.addEventListener('mousedown', handler);
    return () => window.removeEventListener('mousedown', handler);
  }, [!!linkState]);

  useEffect(() => {
    const handler = (e: Event) => {
      const { id, url, rect: passedRect } = (e as CustomEvent).detail;
      if (id !== nodeId) return;
      if (passedRect) {
        setEditLinkRect({ url: url || '', rect: passedRect });
        return;
      }
      const sel = window.getSelection();
      if (sel && sel.rangeCount > 0) {
        const r = sel.getRangeAt(0).getBoundingClientRect();
        if (r.width > 0 || r.height > 0) { setEditLinkRect({ url: url || '', rect: r }); return; }
      }
      const linkEl = document.querySelector(`[data-note-id="${nodeId}"] a.note-link`) as HTMLElement;
      const rect = linkEl?.getBoundingClientRect() ?? new DOMRect(window.innerWidth / 2, 200, 0, 0);
      setEditLinkRect({ url: url || '', rect });
    };
    window.addEventListener('note:edit-link', handler);
    return () => window.removeEventListener('note:edit-link', handler);
  }, [nodeId]);

  const handleFormatChange = useCallback((state: FormatState) => {
    onFormatChange?.(state);
    if (state.linkUrl) {
      requestAnimationFrame(() => {
        if (removingLink.current) { removingLink.current = false; return; }
        const sel = window.getSelection();
        if (sel && sel.rangeCount > 0) {
          const rect = sel.getRangeAt(0).getBoundingClientRect();
          if (rect.width > 0 || rect.height > 0) {
            setLinkState({ url: state.linkUrl!, rect });
            return;
          }
        }
        // fallback: find the link element in the correct editor
        const editorEl = document.querySelector(`[data-note-id="${nodeId}"] a.note-link`) as HTMLElement;
        if (editorEl) {
          setLinkState({ url: state.linkUrl!, rect: editorEl.getBoundingClientRect() });
        }
      });
    } else {
      setLinkState(null);
    }
  }, [onFormatChange]);

  return (
    <>
      <style>{NOTE_STYLES}</style>
      <LexicalComposer initialConfig={{ namespace: `note-${nodeId}`, theme, nodes: NOTE_NODES, onError: console.error }}>
        <div style={{ position: 'relative' }} data-note-id={nodeId}>
          <RichTextPlugin
            contentEditable={
              <ContentEditable
                style={{ minHeight: 60, outline: 'none', resize: 'none', fontSize: '12px', lineHeight: '18px', color: '#374151', overflow: 'auto', cursor: 'auto' }}
              />
            }
            placeholder={
              <div style={{ position: 'absolute', top: 0, left: 0, color: '#9CA3AF', lineHeight: '18px', pointerEvents: 'none' }}>
                {t('workflow.config.notes.placeholder')}
              </div>
            }
            ErrorBoundary={LexicalErrorBoundary}
          />
          <HistoryPlugin />
          <ListPlugin />
          <LinkPlugin />
          <OnChangePlugin onChange={(editorState) => onChange(JSON.stringify(editorState.toJSON()))} />
          <NoteInitPlugin value={value} />
          <NoteFormatPlugin nodeId={nodeId} fontSize={fontSize} onFormatChange={handleFormatChange} />
          {editLinkRect && (
            <EditLinkPopover
              rect={editLinkRect.rect}
              initialUrl={editLinkRect.url}
              onConfirm={(url) => {
                removingLink.current = true;
                window.dispatchEvent(new CustomEvent('note:format', { detail: { id: nodeId, format: 'link', value: url || null } }));
                setEditLinkRect(null);
              }}
            />
          )}
          {linkState && (
            <LinkPopover
              url={linkState.url}
              rect={linkState.rect}
              onEdit={() => {
                removingLink.current = true;
                const { rect, url } = linkState;
                setLinkState(null);
                setEditLinkRect({ url, rect });
              }}
              onRemove={() => {
                removingLink.current = true;
                setLinkState(null);
                window.dispatchEvent(new CustomEvent('note:format', { detail: { id: nodeId, format: 'link', value: null } }));
              }}
            />
          )}
        </div>
      </LexicalComposer>
    </>
  );
};

export default NoteEditor;
