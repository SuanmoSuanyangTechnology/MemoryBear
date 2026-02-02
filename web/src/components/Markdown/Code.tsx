/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:15:05 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:15:05 
 */
/**
 * Code Component
 * 
 * A versatile code rendering component that supports:
 * - Syntax-highlighted code blocks
 * - ECharts visualizations
 * - SVG rendering
 * - Mermaid diagrams
 * - Inline code snippets
 * 
 * @component
 */

import { type FC, useMemo } from 'react'
import SyntaxHighlighter from 'react-syntax-highlighter';
import { atelierHeathLight } from 'react-syntax-highlighter/dist/esm/styles/hljs';
import ReactEcharts from 'echarts-for-react';

import CopyBtn from './CopyBtn';
import Svg from './Svg'
import MermaidChart from './MermaidChart'

/** Props interface for Code component */
type ICodeProps = {
  children: string;
  className: string;
}

/** Code block component that renders syntax-highlighted code or special visualizations */
const Code: FC<ICodeProps> = (props) => {
  const { children, className } = props;
  /** Extract language from className (e.g., 'language-javascript' -> 'javascript') */
  const language = className?.split('-')[1]
  console.log('Code', props)

  // Parse ECharts configuration from code content
  const charData = useMemo(() => {
    if (language !== 'echarts') return null;
    try {
      return JSON.parse(String(children).replace(/\n$/, ''))
    } catch (error) {
      console.error('Error parsing JSON for ECharts:', error)
      return {"title":{"text":"ECharts error - Wrong JSON format."}}
    }
  }, [language, children])

  // Render ECharts visualization
  if (language === 'echarts') {
    return (
      <ReactEcharts
        option={charData}
        style={{
          height: '400px',
          width: '100%',
        }}
      />
    )
  }

  // Render SVG content
  if (language === 'svg') {
    return (
      <Svg
        content={children.replace(/\n/g, '')}
      />
    )
  }
  // Render Mermaid diagram
  if (language === 'mermaid') {
    return (
      <MermaidChart
        content={String(children).replace(/\n$/, '')}
      />
    )
  }
  
  // Render syntax-highlighted code block with copy button
  if (className) {
    return (
      <div className="rb:relative">
        <SyntaxHighlighter
          style={atelierHeathLight}
          customStyle={{
            padding: '16px 20px 16px 24px',
            backgroundColor: '#F0F3F8',
            borderRadius: 8,
          }}
          language={language}
          showLineNumbers={false}
          PreTag="div"
        >
          {children}
        </SyntaxHighlighter>
        <CopyBtn
          value={children}
          style={{
          position: 'absolute',
          top: 20,
          right: 20,
        }}
      />
    </div>
    )
  }
  // Render inline code
  return <code className="rb:bg-[#F0F3F8] rb:px-1 rb:py-0.5 rb:rounded rb:text-sm rb:font-mono rb:whitespace-break-spaces">{children}</code>
}

export default Code
