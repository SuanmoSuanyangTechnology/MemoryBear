/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-15 14:00:07
 */
/**
 * Rich text editor component using Lexical framework
 * Provides text editing with insert, append, clear, and scroll capabilities
 */

import {forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import clsx from 'clsx';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
import { $getSelection, $getRoot, $createParagraphNode, $createTextNode, $isParagraphNode, $isTextNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

import InitialValuePlugin from './plugin/InitialValuePlugin'
import LineBreakPlugin from './plugin/LineBreakPlugin';
import InsertTextPlugin from './plugin/InsertTextPlugin';
import EditablePlugin from './plugin/EditablePlugin';

/**
 * Editor ref methods exposed to parent components
 */
export interface EditorRef {
  /** Insert text at current cursor position */
  insertText: (text: string) => void;
  /** Append text to the end of content */
  appendText: (text: string) => void;
  /** Clear all editor content */
  clear: () => void;
  /** Scroll editor to bottom */
  scrollToBottom: () => void;
}

/**
 * Editor component props
 */
interface LexicalEditorProps {
  /** Additional CSS class names */
  className?: string;
  /** Placeholder text when editor is empty */
  placeholder?: string;
  /** Initial editor value */
  value?: string;
  /** Callback when content changes */
  onChange?: (value: string) => void;
  /** Editor height in pixels */
  height?: string;
  disabled?: boolean;
}

/**
 * Lexical editor theme configuration
 */
const theme = {
  paragraph: 'editor-paragraph',
  text: {
    bold: 'editor-text-bold',
    italic: 'editor-text-italic',
  },
};

/**
 * Editor content component with Lexical context
 */
const EditorContent = forwardRef<EditorRef, LexicalEditorProps>(({
  className = '',
  value,
  placeholder = "Please enter content...",
  onChange,
  disabled,
  height
}, ref) => {
  const [editor] = useLexicalComposerContext();
  const scrollRef = useRef<HTMLDivElement>(null);
  const pendingTextRef = useRef<string>('');
  const rafRef = useRef<number | null>(null);
  const isAppendingRef = useRef(false);
  const scrollTopRef = useRef(0);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onPointerDown = () => {
      if (!isAppendingRef.current) scrollTopRef.current = el.scrollTop;
    };
    el.addEventListener('pointerdown', onPointerDown);
    return () => el.removeEventListener('pointerdown', onPointerDown);
  }, []);

  useEffect(() => {
    return editor.registerUpdateListener(({ tags }) => {
      if (!scrollRef.current) return;
      if (tags.has('append-text')) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      } else {
        scrollRef.current.scrollTop = scrollTopRef.current;
      }
    });
  }, [editor]);

  const scrollToBottom = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  };
  
  /**
   * Expose editor methods to parent component
   * - insertText: Insert at cursor position
   * - appendText: Append to end of content
   * - clear: Clear all content
   * - scrollToBottom: Scroll to bottom
   */
  useImperativeHandle(ref, () => ({
    insertText: (text: string) => {
      editor.update(() => {
        const selection = $getSelection();
        if (selection) {
          selection.insertText(text);
        }
      });
    },
    appendText: (text: string) => {
      pendingTextRef.current += text;
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        const batch = pendingTextRef.current;
        pendingTextRef.current = '';
        if (scrollRef.current) scrollTopRef.current = scrollRef.current.scrollTop;
        isAppendingRef.current = true;
        editor.update(() => {
          const root = $getRoot();
          const lastChild = root.getLastChild();
          if (lastChild && $isParagraphNode(lastChild)) {
            const lastTextNode = lastChild.getLastChild();
            if (lastTextNode && $isTextNode(lastTextNode)) {
              lastTextNode.setTextContent(lastTextNode.getTextContent() + batch);
            } else {
              lastChild.append($createTextNode(batch));
            }
          } else {
            const paragraph = $createParagraphNode();
            paragraph.append($createTextNode(batch));
            root.append(paragraph);
          }
        }, {
          tag: 'append-text',
          onUpdate: () => { isAppendingRef.current = false; }
        });
      });
    },
    clear: () => {
      editor.update(() => {
        const root = $getRoot();
        root.clear();
        const paragraph = $createParagraphNode();
        root.append(paragraph);
      });
    },
    scrollToBottom,
  }), [editor]);

  return (
    <div ref={scrollRef} style={{ position: 'relative' }} className={height ? `${height} rb:overflow-y-auto` : ''}>
      <RichTextPlugin
        contentEditable={
          <ContentEditable
            className={clsx(
              "rb:outline-none rb:resize-none rb:text-[14px] rb:leading-5 rb:px-4 rb:py-5 rb:bg-[#FBFDFF] rb-border rb:rounded-lg",
              disabled && "rb:cursor-not-allowed rb:bg-[#F6F8FC] rb:text-[#5B6167]",
              className
            )}
          />
        }
        placeholder={
          <div className="rb:absolute rb:top-0 rb:px-4 rb:py-5 rb:text-[14px] rb:text-[#5B6167] rb:leading-5 rb:pointer-none">
            {placeholder}
          </div>
        }
        ErrorBoundary={LexicalErrorBoundary}
      />
      <LineBreakPlugin onChange={onChange} />
      <InitialValuePlugin value={value} />
      <InsertTextPlugin />
      <EditablePlugin disabled={disabled} />
    </div>
  );
});

/**
 * Main editor wrapper component
 * Initializes Lexical composer with configuration
 */
const Editor = forwardRef<EditorRef, LexicalEditorProps>((props, ref) => {
  const initialConfig = {
    namespace: 'Editor',
    theme,
    nodes: [],
    editable: !props.disabled,
    onError: (error: Error) => {
      console.error(error);
    },
  };

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <EditorContent {...props} ref={ref} />
    </LexicalComposer>
  );
});

export default Editor;