import { type FC, useState } from 'react';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
// import { AutoFocusPlugin } from '@lexical/react/LexicalAutoFocusPlugin';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';
// import { HeadingNode, QuoteNode } from '@lexical/rich-text';
// import { ListItemNode, ListNode } from '@lexical/list';
// import { LinkNode } from '@lexical/link';
// import { CodeNode } from '@lexical/code';

import AutocompletePlugin, { type Suggestion } from './plugin/AutocompletePlugin'
import CharacterCountPlugin from './plugin/CharacterCountPlugin'
import InitialValuePlugin from './plugin/InitialValuePlugin';
import CommandPlugin from './plugin/CommandPlugin';
import { VariableNode } from './nodes/VariableNode'

interface LexicalEditorProps {
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  suggestions: Suggestion[];
}

const theme = {
  paragraph: 'editor-paragraph',
  text: {
    bold: 'editor-text-bold',
    italic: 'editor-text-italic',
  },
};

const Editor: FC<LexicalEditorProps> =({
  placeholder = "请输入内容...",
  value = "",
  onChange,
  suggestions,
}) => {
  const [_count, setCount] = useState(0);
  const initialConfig = {
    namespace: 'AutocompleteEditor',
    theme,
    nodes: [
      // HeadingNode,
      // QuoteNode,
      // ListItemNode,
      // ListNode,
      // LinkNode,
      // CodeNode,
      VariableNode
    ],
    onError: (error: Error) => {
      console.error(error);
    },
  };

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <div style={{ position: 'relative' }}>
        <RichTextPlugin
          contentEditable={
            <ContentEditable
              style={{
                minHeight: '60px',
                padding: '0',
                border: 'none',
                outline: 'none',
                resize: 'none',
                fontSize: '14px',
                lineHeight: '20px',
              }}
            />
          }
          placeholder={
            <div
              style={{
                position: 'absolute',
                top: '0',
                left: '0',
                color: '#5B6167',
                fontSize: '14px',
                lineHeight: '20px',
                pointerEvents: 'none',
              }}
            >
              {placeholder}
            </div>
          }
          ErrorBoundary={LexicalErrorBoundary}
        />
        <HistoryPlugin />
        <CommandPlugin />
        <AutocompletePlugin suggestions={suggestions} />
        <CharacterCountPlugin setCount={(count) => { setCount(count) }} onChange={onChange} />
        <InitialValuePlugin value={value} suggestions={suggestions} />
      </div>
    </LexicalComposer>
  );
};

export default Editor;