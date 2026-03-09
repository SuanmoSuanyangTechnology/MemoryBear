import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { FORMAT_TEXT_COMMAND, $getSelection, $isRangeSelection, $setSelection, $isTextNode, type BaseSelection } from 'lexical';
import { $patchStyleText } from '@lexical/selection';
import { INSERT_UNORDERED_LIST_COMMAND, REMOVE_LIST_COMMAND, ListNode } from '@lexical/list';
import { TOGGLE_LINK_COMMAND, LinkNode } from '@lexical/link';
import { $getNearestNodeOfType } from '@lexical/utils';

export const NOTE_FORMAT_EVENT = 'note:format';

export interface FormatState {
  bold: boolean;
  italic: boolean;
  strikethrough: boolean;
  list: boolean;
  fontSize?: number;
  linkUrl?: string | null;
}

const NoteFormatPlugin = ({ nodeId, onFormatChange, fontSize = 12 }: { nodeId: string; fontSize?: number; onFormatChange?: (state: FormatState) => void }) => {
  const [editor] = useLexicalComposerContext();
  const savedSelection = useRef<BaseSelection | null>(null);

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const selection = $getSelection();
        if (!$isRangeSelection(selection)) return;
        savedSelection.current = selection.clone();
        const anchorNode = selection.anchor.getNode();
        const style = 'getStyle' in anchorNode ? (anchorNode as { getStyle(): string }).getStyle() : '';
        const match = style.match(/font-size:\s*([\d.]+)px/);
        const nodeFontSize = match ? Number(match[1]) : fontSize;
        const linkNode = $getNearestNodeOfType(anchorNode, LinkNode);
        onFormatChange?.({
          bold: selection.hasFormat('bold'),
          italic: selection.hasFormat('italic'),
          strikethrough: selection.hasFormat('strikethrough'),
          list: !!$getNearestNodeOfType(anchorNode, ListNode),
          ...(nodeFontSize ? { fontSize: nodeFontSize } : {}),
          linkUrl: linkNode ? linkNode.getURL() : null,
        });
      });
    });
  }, [editor, onFormatChange]);

  useEffect(() => {
    const handler = (e: Event) => {
      const { id, format, value } = (e as CustomEvent).detail;
      if (id !== nodeId) return;
      const sel = savedSelection.current;
      const hasSelection = $isRangeSelection(sel) && !sel.isCollapsed();
      if (format === 'link' && value === null) {
        // remove link: select the entire LinkNode first
        editor.focus(() => {
          editor.update(() => {
            const s = $getSelection();
            const anchorNode = $isRangeSelection(s)
              ? s.anchor.getNode()
              : savedSelection.current && $isRangeSelection(savedSelection.current)
                ? savedSelection.current.anchor.getNode()
                : null;
            const linkNode = anchorNode ? $getNearestNodeOfType(anchorNode, LinkNode) : null;
            if (linkNode) {
              const children = linkNode.getChildren();
              if (children.length > 0) {
                const first = children[0];
                const last = children[children.length - 1];
                if ($isTextNode(first) && $isTextNode(last)) {
                  const range = first.select(0, 0);
                  range.focus.set(last.getKey(), last.getTextContentSize(), 'text');
                }
              }
            }
          });
          editor.dispatchCommand(TOGGLE_LINK_COMMAND, null);
        });
      } else if (format === 'list') {
        editor.focus(() => {
          if (sel) editor.update(() => $setSelection(sel));
          editor.dispatchCommand(value ? INSERT_UNORDERED_LIST_COMMAND : REMOVE_LIST_COMMAND, undefined);
          editor.update(() => $setSelection(null));
        });
      } else if (hasSelection) {
        editor.focus(() => {
          editor.update(() => $setSelection(sel));
          if (format === 'bold' || format === 'italic' || format === 'strikethrough') {
            editor.dispatchCommand(FORMAT_TEXT_COMMAND, format);
          } else if (format === 'link') {
            editor.dispatchCommand(TOGGLE_LINK_COMMAND, value as string | null);
          } else if (format === 'fontSize') {
            editor.update(() => {
              $setSelection(sel);
              $patchStyleText(sel!, { 'font-size': `${value}px` });
            });
          }
          editor.update(() => $setSelection(null));
        });
      }
    };
    window.addEventListener(NOTE_FORMAT_EVENT, handler);
    return () => window.removeEventListener(NOTE_FORMAT_EVENT, handler);
  }, [editor, nodeId]);

  return null;
};

export default NoteFormatPlugin;
