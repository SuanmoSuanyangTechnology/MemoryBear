/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-03 16:18:14
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
import InsertFormFieldPlugin from './plugin/InsertFormFieldPlugin';
import { VariableNode } from './nodes/VariableNode'
import { FormFieldNode } from './nodes/FormFieldNode';
import { FormFieldProvider } from './nodes/FormFieldContext';
import Jinja2Editor from './Jinja2Editor';


export interface FormField {
  id: string;
  default_value?: string;
  variable_ref?: string;
}
// Props interface for Lexical Editor component
export interface LexicalEditorProps {
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  options?: Suggestion[];
  variant?: 'outlined' | 'borderless' | 'filled';
  height?: number;
  fontSize?: number;
  lineHeight?: number;
  size?: 'default' | 'small';
  type?: 'input' | 'textarea';
  language?: 'string' | 'jinja2';
  className?: string;
  waitForInit?: boolean;
  updateFormFields?: (form_fields: FormField[]) => void;
  formFields?: FormField[];
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
  className,
  updateFormFields,
  formFields = [],
}) => {
  console.log('Editor value', value)
  const [_count, setCount] = useState(0);
  const [focused, setFocused] = useState(false);

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
    nodes: [VariableNode, FormFieldNode],
    onError: (error: Error) => {
      console.error(error);
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), []);

  // Calculate minimum height based on type and size
  const minheight = useMemo(() => {
    if (type === 'input') {
      return `${height ? height : size === 'small' && ['borderless', 'filled'].includes(variant) ? 18 : size === 'small' ? 26 : 30}px`
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
  }, [size, height, variant])

  // Calculate placeholder minimum height
  const placeHolderMinheight = useMemo(() => {
    return `${height ? 16 : size === 'small' ? 16 : 30}px`
  }, [type, size, height])

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <FormFieldProvider 
        updateFormFields={updateFormFields || ((_) => {})} 
        formFields={formFields}
        options={options}
      >
        <div style={{ position: 'relative', borderRadius: '8px', background: variant === 'filled' ? '#F6F6F6': 'transparent' }} className={className}>
          <RichTextPlugin
            contentEditable={
              <ContentEditable
                style={{
                  minHeight: minheight,
                  padding: height ? '4px 6px' : variant === 'outlined' ? '6px 8px': '0',
                  border: type === 'input' && focused
                    ? '1px solid #171719'
                    : variant === 'outlined' ? '1px solid #EBEBEB' : 'none',
                  borderRadius: '8px',
                  outline: 'none',
                  resize: 'none',
                  fontSize: fontSize,
                  lineHeight: lineHeight,
                }}
                onFocus={() => type === 'input' && setFocused(true)}
                onBlur={() => type === 'input' && setFocused(false)}
              />
            }
            placeholder={
              <div
                style={{
                  minHeight: placeHolderMinheight,
                  position: 'absolute',
                  top: variant === 'outlined' ? '6px' : type === 'input' ? '6px' : '2px',
                  left: variant === 'outlined' ? '11px' : type === 'input' ? '8px' : '0',
                  color: 'rgba(23,23,25,0.25)',
                  fontSize: fontSize,
                  lineHeight: placeHolderMinheight,
                  pointerEvents: 'none',
                  borderRadius: '8px',
                }}
              >
                {placeholder}
              </div>
            }
            ErrorBoundary={LexicalErrorBoundary}
          />
          <HistoryPlugin />
          <CommandPlugin />
          <AutocompletePlugin options={options} />
          <CharacterCountPlugin setCount={setCount} />
          <InitialValuePlugin value={value} options={options} formFields={formFields} onChange={onChange} />
          <BlurPlugin />
          {updateFormFields &&
            <InsertFormFieldPlugin
              formFields={formFields}
              updateFormFields={updateFormFields}
              options={options}
            />
          }
        </div>
      </FormFieldProvider>
    </LexicalComposer>
  );
};

export default Editor;
