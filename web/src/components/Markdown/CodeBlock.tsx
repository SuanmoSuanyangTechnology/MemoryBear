/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:15:11 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:15:11 
 */
/**
 * CodeBlock Component
 * 
 * A standalone code block component for displaying formatted code with:
 * - Syntax highlighting
 * - Optional copy functionality
 * - Configurable size and line numbers
 * 
 * @component
 */

import { type FC } from 'react'
import SyntaxHighlighter from 'react-syntax-highlighter';
import { atelierHeathLight } from 'react-syntax-highlighter/dist/esm/styles/hljs';

import CopyBtn from './CopyBtn';

/** Props interface for CodeBlock component */
type ICodeBlockProps = {
  value: string;
  needCopy?: boolean;
  size?: 'small' | 'default';
  showLineNumbers?: boolean;
}

/** Code block component for displaying formatted code with optional copy functionality */
const CodeBlock: FC<ICodeBlockProps> = ({
  value,
  needCopy = true,
  size = 'default',
  showLineNumbers = false
}) => {

  return (
    <div className="rb:relative">
      <SyntaxHighlighter
        style={atelierHeathLight}
        customStyle={{
          padding: '8px 12px 8px 12px',
          backgroundColor: '#F0F3F8',
          borderRadius: 8,
          fontSize: size === 'small' ? 12 : 14,
          wordBreak: 'break-all'
        }}
        language="json"
        showLineNumbers={showLineNumbers}
        PreTag="div"
      >
        {value}
      </SyntaxHighlighter>
      {needCopy && <CopyBtn
        value={value}
        style={{
          position: 'absolute',
          top: 20,
          right: 20,
        }}
      />}
    </div>
  )
}

export default CodeBlock
