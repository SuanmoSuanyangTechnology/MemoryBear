/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:17 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:25:17 
 */
/**
 * Rich text editor component using Lexical framework
 * Provides text editing with insert, append, clear, and scroll capabilities
 */

import {forwardRef, useImperativeHandle } from 'react';
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
  height?: number;
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
  disabled
}, ref) => {
  const [editor] = useLexicalComposerContext();
  
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
      editor.update(() => {
        const root = $getRoot();
        const lastChild = root.getLastChild();
        if (lastChild && $isParagraphNode(lastChild)) {
          const lastTextNode = lastChild.getLastChild();
          if (lastTextNode && $isTextNode(lastTextNode)) {
            const currentText = lastTextNode.getTextContent();
            lastTextNode.setTextContent(currentText + text);
          } else {
            const textNode = $createTextNode(text);
            lastChild.append(textNode);
          }
        } else {
          const paragraph = $createParagraphNode();
          const textNode = $createTextNode(text);
          paragraph.append(textNode);
          root.append(paragraph);
        }
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
    scrollToBottom: () => {
      const editorElement = editor.getRootElement();
      if (editorElement) {
        editorElement.scrollTop = editorElement.scrollHeight;
      }
    }
  }), [editor]);

  return (
    <div style={{ position: 'relative' }}>
      <RichTextPlugin
        contentEditable={
          <ContentEditable
            className={clsx(
              "rb:outline-none rb:resize-none rb:text-[14px] rb:leading-5 rb:px-4 rb:py-5 rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:overflow-auto",
              disabled && "rb:cursor-not-allowed rb:bg-[#F6F8FC] rb:text-[#5B6167]",
              className
            )}
          />
        }
        placeholder={
          <div className="rb:absolute rb:px-4 rb:py-5 rb:text-[14px] rb:text-[#5B6167] rb:leading-5 rb:pointer-none">
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