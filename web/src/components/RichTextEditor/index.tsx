import { forwardRef } from 'react';
import clsx from 'clsx';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { ListPlugin } from '@lexical/react/LexicalListPlugin';
import { OnChangePlugin } from '@lexical/react/LexicalOnChangePlugin';
import { EditorRefPlugin } from '@lexical/react/LexicalEditorRefPlugin';
import { ListNode, ListItemNode } from '@lexical/list';
import { Flex } from 'antd';
import { MarkdownPastePlugin } from './MarkdownPastePlugin';
import { MarkdownCopyPlugin } from './MarkdownCopyPlugin';
import { Toolbar } from './Toolbar';
import { useRichTextEditor } from './useRichTextEditor';

interface RichTextEditorProps {
  value?: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  maxLength?: number;
  className?: string;
  disabled?: boolean;
}

export interface RichTextEditorRef {
  clear: () => void;
  getValue: () => string;
  setValue: (value: string) => void;
}

const RichTextEditor = forwardRef<RichTextEditorRef, RichTextEditorProps>(({
  value = '',
  onChange,
  placeholder = 'Enter content...',
  maxLength = 2000,
  className,
  disabled = false,
}, ref) => {
  const { editorRef, charCount, handleChange } = useRichTextEditor({ value, onChange, maxLength }, ref);

  const initialConfig = {
    namespace: 'RichTextEditor',
    theme: {
      text: {
        bold: 'rb:font-bold',
        italic: 'rb:italic',
        underline: 'rb:underline rb:decoration-1 rb:underline-offset-2',
      },
      list: {
        ul: 'rb:list-disc rb:pl-6 rb:my-2',
        ol: 'rb:list-decimal rb:pl-6 rb:my-2',
        listitem: 'rb:my-1 rb:pl-1',
        nested: {
          listitem: 'rb:my-1',
        },
      },
    },
    nodes: [ListNode, ListItemNode],
    editable: !disabled,
    onError: (error: Error) => {
      console.error(error);
    },
  };

  return (
    <div className={clsx("rb:border rb:border-gray-200 rb:rounded-lg rb:overflow-hidden", className)}>
      <LexicalComposer initialConfig={initialConfig}>
        <EditorRefPlugin editorRef={editorRef} />
        <Toolbar />
        <MarkdownPastePlugin />
        <MarkdownCopyPlugin />
        <div className="rb:relative">
          <RichTextPlugin
            contentEditable={
              <ContentEditable 
                className={clsx(
                  "rb:min-h-40 rb:p-4 rb:outline-none rb:text-sm rb:leading-relaxed",
                  disabled && "rb:bg-gray-100 rb:cursor-not-allowed"
                )} 
              />
            }
            placeholder={
              <div className="rb:absolute rb:top-4 rb:left-4 rb:text-gray-400 rb:text-sm rb:pointer-events-none">
                {placeholder}
              </div>
            }
            ErrorBoundary={LexicalErrorBoundary}
          />
          <OnChangePlugin onChange={handleChange} />
          <HistoryPlugin />
          <ListPlugin />
        </div>
      </LexicalComposer>
      <Flex justify="flex-end" className="rb:px-3! rb:py-2! rb:text-[12px] rb:text-gray-600">
        {charCount}/{maxLength}
      </Flex>
    </div>
  );
});

RichTextEditor.displayName = 'RichTextEditor';

export default RichTextEditor;
