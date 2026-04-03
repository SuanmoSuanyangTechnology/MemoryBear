/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-03 20:44:16
 */
import { type FC, useState, useMemo } from 'react';
import { LexicalComposer } from '@lexical/react/LexicalComposer';
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin';
import { ContentEditable } from '@lexical/react/LexicalContentEditable';
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin';
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary';

import AutocompletePlugin, { type Suggestion } from './plugin/AutocompletePlugin'
import CharacterCountPlugin from './plugin/CharacterCountPlugin'
import InitialValuePlugin from './plugin/InitialValuePlugin';
import CommandPlugin from './plugin/CommandPlugin';
import BlurPlugin from './plugin/BlurPlugin';
import { VariableNode } from './nodes/VariableNode'
import Jinja2Editor from './Jinja2Editor';

// Props interface for Lexical Editor component
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
  type?: 'input' | 'textarea';
  language?: 'string' | 'jinja2';
  className?: string;
}

// Default theme for editor
const theme = {
  paragraph: 'editor-paragraph',
  text: {
    bold: 'editor-text-bold',
    italic: 'editor-text-italic',
  },
};

// Main Lexical Editor component
const Editor: FC<LexicalEditorProps> =({
  placeholder = "请输入内容...",
  value = "",
  onChange,
  options = [],
  variant = 'borderless',
  size = 'default',
  type = 'textarea',
  language = 'string',
  height,
  className
}) => {
  const [_count, setCount] = useState(0);

  if (language === 'jinja2') {
    return (
      <Jinja2Editor
        placeholder={placeholder}
        value={value}
        onChange={onChange}
        options={options}
        variant={variant}
        size={size}
        height={height}
        className={className}
      />
    );
  }

  // Lexical editor configuration — must be stable (never recreated)
  const initialConfig = useMemo(() => ({
    namespace: 'AutocompleteEditor',
    theme,
    nodes: [VariableNode],
    onError: (error: Error) => {
      console.error(error);
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), []);

  // Calculate minimum height based on type and size
  const minheight = useMemo(() => {
    if (type === 'input') {
      return `${height ? height : size === 'small' && variant === 'borderless' ? 18 : size === 'small' ? 26 : 30}px`
    }
    return `${height ? height : size === 'small' ? 60 : 120}px`
  }, [type, size, height, variant])

  // Calculate font size based on size prop
  const fontSize = useMemo(() => {
    return `${size === 'small' ? 12 : 14}px`
  }, [size])

  // Calculate line height based on size prop
  const lineHeight = useMemo(() => {
    return `${height ? height - 10 : size === 'small' && variant === 'borderless' ? 18 : size === 'small' ? 16 : 20}px`
  }, [size])

  // Calculate placeholder minimum height
  const placeHolderMinheight = useMemo(() => {
    return `${height ? 16 : size === 'small' ? 16 : 30}px`
  }, [type, size, height])

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <div style={{ position: 'relative' }} className={className}>
        <RichTextPlugin
          contentEditable={
            <ContentEditable
              style={{
                minHeight: minheight,
                padding: height ? '4px 6px' : variant === 'borderless' ? '0' : '6px 8px',
                border: variant === 'borderless' ? 'none' : '1px solid #EBEBEB',
                borderRadius: '8px',
                outline: 'none',
                resize: 'none',
                fontSize: fontSize,
                lineHeight: lineHeight,
              }}
            />
          }
          placeholder={
            <div
              style={{
                minHeight: placeHolderMinheight,
                position: 'absolute',
                top: variant === 'borderless' ? '2px' : '6px',
                left: variant === 'borderless' ? '0' : '11px',
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
        <AutocompletePlugin options={options} enableJinja2={false} />
        <CharacterCountPlugin setCount={setCount} onChange={onChange} />
        <InitialValuePlugin value={value} options={options} enableLineNumbers={false} />
        <BlurPlugin enableJinja2={false} />
      </div>
    </LexicalComposer>
  );
};

export default Editor;
