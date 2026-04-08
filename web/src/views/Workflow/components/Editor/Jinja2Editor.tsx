/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-02 15:15:36 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 14:48:00
 */
import { type FC, useEffect, useMemo } from 'react';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';

import { type Suggestion } from './plugin/AutocompletePlugin';
import Jinjia2CharacterCountPlugin from './plugin/Jinjia2CharacterCountPlugin';
import Jinja2InitialValuePlugin from './plugin/Jinja2InitialValuePlugin';
import Jinja2AutocompletePlugin from './plugin/Jinja2AutocompletePlugin';
import Jinja2HighlightPlugin from './plugin/Jinja2HighlightPlugin';
import Jinja2BlurPlugin from './plugin/Jinja2BlurPlugin';
import LineNumberPlugin from './plugin/LineNumberPlugin';

const jinja2Theme = {
  paragraph: 'editor-paragraph',
  code: 'jinja2-expression',
  text: {
    bold: 'editor-text-bold',
    italic: 'editor-text-italic',
    code: 'jinja2-inline',
  },
};

const initialConfig = {
  namespace: 'AutocompleteEditor',
  theme: jinja2Theme,
  nodes: [],
  onError: (error: Error) => console.error(error),
};

const STYLE_ID = 'code-editor-styles';
const JINJA2_STYLES = `
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
  .editor-paragraph { margin: 0; }
  .editor-with-line-numbers { display: flex; }
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
  .line-numbers > div { min-height: 20px; display: flex; align-items: flex-start; }
  .editor-content-wrapper { flex: 1; }
  .editor-content-with-numbers {
    white-space: pre-wrap;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  }
  .editor-content-with-numbers p { margin: 0; min-height: 20px; }
`;

export interface Jinja2EditorProps {
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  options?: Suggestion[];
  variant?: 'outlined' | 'borderless';
  height?: number;
  size?: 'default' | 'small';
  className?: string;
}

const Jinja2Editor: FC<Jinja2EditorProps> = ({
  placeholder = '请输入内容...',
  value,
  onChange,
  options = [],
  variant = 'borderless',
  size = 'default',
  height,
  className,
}) => {
  useEffect(() => {
    if (!document.getElementById(STYLE_ID)) {
      const style = document.createElement('style');
      style.id = STYLE_ID;
      style.textContent = JINJA2_STYLES;
      document.head.appendChild(style);
    }
  }, []);

  const minheight = useMemo(
    () => `${height ?? (size === 'small' ? 60 : 120)}px`,
    [height, size],
  );

  const fontSize = size === 'small' ? '12px' : '14px';

  const lineHeight = useMemo(
    () => `${height ? height - 10 : size === 'small' ? 16 : 20}px`,
    [height, size],
  );

  const placeHolderMinheight = `${height ? 16 : size === 'small' ? 16 : 30}px`;

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <div style={{ position: 'relative' }} className={className}>
        <RichTextPlugin
          contentEditable={
            <div
              className="editor-with-line-numbers"
              style={{
                border: variant === 'borderless' ? 'none' : '1px solid #DFE4ED',
                borderRadius: '6px',
                minHeight: minheight,
              }}
            >
              <div className="line-numbers">
                <div>1</div>
              </div>
              <div className="editor-content-wrapper">
                <ContentEditable
                  className="editor-content-with-numbers"
                  style={{
                    minHeight: minheight,
                    padding: variant === 'borderless' ? '0' : '4px 0',
                    outline: 'none',
                    resize: 'none',
                    fontSize,
                    lineHeight,
                    border: 'none',
                  }}
                />
              </div>
            </div>
          }
          placeholder={
            <div
              style={{
                minHeight: placeHolderMinheight,
                position: 'absolute',
                top: '4px',
                left: '16px',
                color: '#A8A9AA',
                fontSize,
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
        <Jinja2HighlightPlugin />
        <LineNumberPlugin />
        <Jinja2AutocompletePlugin options={options} />
        <Jinjia2CharacterCountPlugin setCount={() => {}} />
        <Jinja2InitialValuePlugin value={value} onChange={onChange} />
        <Jinja2BlurPlugin />
      </div>
    </LexicalComposer>
  );
};

export default Jinja2Editor;
