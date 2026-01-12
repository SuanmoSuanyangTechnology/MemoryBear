import { type FC, useState, useEffect } from 'react';
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
import Jinja2HighlightPlugin from './plugin/Jinja2HighlightPlugin';
import LineNumberPlugin from './plugin/LineNumberPlugin';
import { VariableNode } from './nodes/VariableNode'

interface LexicalEditorProps {
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  options: Suggestion[];
  variant?: 'outlined' | 'borderless';
  height?: number;
  enableJinja2?: boolean;
}

const theme = {
  paragraph: 'editor-paragraph',
  text: {
    bold: 'editor-text-bold',
    italic: 'editor-text-italic',
  },
};

const jinja2Theme = {
  ...theme,
  code: 'jinja2-expression',
  text: {
    ...theme.text,
    code: 'jinja2-inline',
  },
};

const Editor: FC<LexicalEditorProps> =({
  placeholder = "请输入内容...",
  value = "",
  onChange,
  options,
  variant = 'borderless',
  height = 60,
  enableJinja2 = false,
}) => {

  const [_count, setCount] = useState(0);

  useEffect(() => {
    if (enableJinja2) {
      const styleId = 'jinja2-styles';
      let existingStyle = document.getElementById(styleId);
      
      if (!existingStyle) {
        const style = document.createElement('style');
        style.id = styleId;
        style.textContent = `
          .jinja2-expression {
            background-color: #f6f8fa !important;
            border: 1px solid #d1d9e0 !important;
            border-radius: 3px !important;
            padding: 2px 4px !important;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace !important;
            font-size: 13px !important;
            color: #0969da !important;
          }
          .jinja2-inline {
            background-color: #f6f8fa !important;
            padding: 1px 3px !important;
            border-radius: 2px !important;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace !important;
            font-size: 13px !important;
            color: #0969da !important;
          }
          .editor-paragraph {
            margin: 0;
          }
          .editor-paragraph:has-text('{') .editor-text,
          .editor-paragraph:has-text('[') .editor-text {
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace !important;
          }
          .editor-with-line-numbers {
            display: flex;
          }
          .line-numbers {
            background-color: #f8f9fa;
            border-right: 1px solid #e1e4e8;
            color: #656d76;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
            font-size: 12px;
            line-height: 20px;
            padding: 4px 8px;
            text-align: right;
            user-select: none;
            display: flex;
            flex-direction: column;
          }
          .line-numbers > div {
            min-height: 20px;
            display: flex;
            align-items: flex-start;
          }
          .editor-content-with-numbers {
            flex: 1;
            white-space: pre-wrap;
          }
          .editor-content-with-numbers p {
            margin: 0;
            min-height: 20px;
          }
        `;
        document.head.appendChild(style);
      }
    }
  }, [enableJinja2]);
  const initialConfig = {
    namespace: 'AutocompleteEditor',
    theme: enableJinja2 ? jinja2Theme : theme,
    nodes: enableJinja2 ? [
      // 当启用jinja2时，不使用VariableNode，使用普通文本
    ] : [
      // HeadingNode,
      // QuoteNode,
      // ListItemNode,
      // ListNode,
      // LinkNode,
      // CodeNode,
      VariableNode,
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
            enableJinja2 ? (
              <div className="editor-with-line-numbers" style={{
                border: variant === 'borderless' ? 'none' : '1px solid #DFE4ED',
                borderRadius: '6px',
                minHeight: `${height}px`,
              }}>
                <div className="line-numbers">
                  <div>1</div>
                </div>
                <ContentEditable
                  className="editor-content-with-numbers"
                  style={{
                    minHeight: `${height}px`,
                    padding: '4px 11px',
                    outline: 'none',
                    resize: 'none',
                    fontSize: '14px',
                    lineHeight: '20px',
                    border: 'none',
                  }}
                />
              </div>
            ) : (
              <ContentEditable
                style={{
                  minHeight: `${height}px`,
                  padding: variant === 'borderless' ? '0' : '4px 11px',
                  border: variant === 'borderless' ? 'none' : '1px solid #DFE4ED',
                  borderRadius: '6px',
                  outline: 'none',
                  resize: 'none',
                  fontSize: '14px',
                  lineHeight: '20px',
                }}
              />
            )
          }
          placeholder={
            <div
              style={{
                position: 'absolute',
                top: variant === 'borderless' ? '0' : '6px',
                left: enableJinja2 ? '59px' : (variant === 'borderless' ? '0' : '11px'),
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
        {enableJinja2 && <Jinja2HighlightPlugin />}
        {enableJinja2 && <LineNumberPlugin />}
        <AutocompletePlugin options={options} enableJinja2={enableJinja2} />
        <CharacterCountPlugin setCount={(count) => { setCount(count) }} onChange={onChange} />
        <InitialValuePlugin value={value} options={options} enableJinja2={enableJinja2} />
      </div>
    </LexicalComposer>
  );
};

export default Editor;