import {forwardRef, useImperativeHandle } from 'react';
import clsx from 'clsx';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
import { $getSelection } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import InitialValuePlugin from './plugin/InitialValuePlugin'
import LineBreakPlugin from './plugin/LineBreakPlugin';
import InsertTextPlugin from './plugin/InsertTextPlugin';

export interface EditorRef {
  insertText: (text: string) => void;
}

interface LexicalEditorProps {
  className?: string;
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  height?: number;
}

const theme = {
  paragraph: 'editor-paragraph',
  text: {
    bold: 'editor-text-bold',
    italic: 'editor-text-italic',
  },
};

const EditorContent = forwardRef<EditorRef, LexicalEditorProps>(({
  className = '',
  value,
  placeholder = "请输入内容...",
  onChange,
}, ref) => {
  const [editor] = useLexicalComposerContext();
  
  useImperativeHandle(ref, () => ({
    insertText: (text: string) => {
      editor.update(() => {
        const selection = $getSelection();
        if (selection) {
          selection.insertText(text);
        }
      });
    }
  }), [editor]);

  return (
    <div style={{ position: 'relative' }}>
      <RichTextPlugin
        contentEditable={
          <ContentEditable
            className={clsx("rb:outline-none rb:resize-none rb:text-[14px] rb:leading-5 rb:px-4 rb:py-5 rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:overflow-auto", className)}
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
    </div>
  );
});

const Editor = forwardRef<EditorRef, LexicalEditorProps>((props, ref) => {
  const initialConfig = {
    namespace: 'Editor',
    theme,
    nodes: [],
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