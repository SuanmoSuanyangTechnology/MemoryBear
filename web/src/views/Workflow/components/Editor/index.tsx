import { type FC, useState, useEffect, useMemo } from 'react';
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
import BlurPlugin from './plugin/BlurPlugin';
import { VariableNode } from './nodes/VariableNode'

export interface LexicalEditorProps {
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  options?: Suggestion[];
  variant?: 'outlined' | 'borderless';
  height?: number;
  fontSize?: number;
  lineHeight?: number;
  size?: 'default' | 'small';
  type?: 'input' | 'textarea',
  language?: 'string' | 'jinja2'
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
  options = [],
  variant = 'borderless',
  size = 'default',
  type = 'textarea',
  language = 'string'
}) => {
  const [_count, setCount] = useState(0);
  const [enableJinja2, setEnableJinja2] = useState(false)
  const [enableLineNumbers, setEnableLineNumbers] = useState(false)

  useEffect(() => {
    const needsLineNumbers = language === 'jinja2';
    setEnableJinja2(language === 'jinja2');
    setEnableLineNumbers(needsLineNumbers);

    if (needsLineNumbers) {
      const styleId = 'code-editor-styles';
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
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
            font-size: 12px;
            line-height: 16px;
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
          .editor-content-wrapper {
            flex: 1;
          }
          .editor-content-with-numbers {
            white-space: pre-wrap;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
          }
          .editor-content-with-numbers p {
            margin: 0;
            min-height: 20px;
          }
        `;
        document.head.appendChild(style);
      }
    }
  }, [language])

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
  const minheight = useMemo(() => {
    if (type === 'input') {
      return `${size === 'small' ? 26 : 30}px`
    }
    return `${size === 'small' ? 60 : 120}px`
  }, [type, size])
  const fontSize = useMemo(() => {
    return `${size === 'small' ? 12 : 14}px`
  }, [size])
  const lineHeight = useMemo(() => {
    return `${size === 'small' ? 16 : 20}px`
  }, [size])
  const placeHolderMinheight = useMemo(() => {
    return `${size === 'small' ? 16 : 30}px`
  }, [type, size])

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <div style={{ position: 'relative' }}>
        <RichTextPlugin
          contentEditable={
            enableLineNumbers ? (
              <div className="editor-with-line-numbers" style={{
                border: variant === 'borderless' ? 'none' : '1px solid #DFE4ED',
                borderRadius: '6px',
                minHeight: minheight,
              }}>
                <div className="line-numbers">
                  <div>1</div>
                </div>
                <div className="editor-content-wrapper">
                  <ContentEditable
                    className="editor-content-with-numbers"
                    style={{
                      minHeight: minheight,
                      padding: '4px 0',
                      outline: 'none',
                      resize: 'none',
                      fontSize: fontSize,
                      lineHeight: lineHeight,
                      border: 'none',
                    }}
                  />
                </div>
              </div>
            ) : (
              <ContentEditable
                style={{
                  minHeight: minheight,
                  padding: variant === 'borderless' ? '0' : '4px 11px',
                  border: variant === 'borderless' ? 'none' : '1px solid #DFE4ED',
                  borderRadius: '6px',
                  outline: 'none',
                  resize: 'none',
                  fontSize: fontSize,
                  lineHeight: lineHeight,
                }}
              />
            )
          }
          placeholder={
            <div
              style={{
                minHeight: placeHolderMinheight,
                position: 'absolute',
                top: enableLineNumbers ? '4px' : variant === 'borderless' ? '0' : '6px',
                left: enableLineNumbers ? '16px' : (variant === 'borderless' ? '0' : '11px'),
                color: '#A8A9AA',
                fontSize: fontSize,
                lineHeight: placeHolderMinheight,
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
        {language === 'jinja2' && <Jinja2HighlightPlugin />}
        {enableLineNumbers && <LineNumberPlugin />}
        <AutocompletePlugin options={options} enableJinja2={enableJinja2} />
        <CharacterCountPlugin setCount={(count) => { setCount(count) }} onChange={onChange} />
        <InitialValuePlugin value={value} options={options} enableLineNumbers={enableLineNumbers} />
        {enableJinja2 && <BlurPlugin />}
      </div>
    </LexicalComposer>
  );
};

export default Editor;