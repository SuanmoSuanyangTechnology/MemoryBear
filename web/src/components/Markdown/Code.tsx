import { type FC, useMemo } from 'react'
import SyntaxHighlighter from 'react-syntax-highlighter';
import { atelierHeathLight } from 'react-syntax-highlighter/dist/esm/styles/hljs';
import CopyBtn from './CopyBtn';
import ReactEcharts from 'echarts-for-react';
import Svg from './Svg'
import MermaidChart from './MermaidChart'


type ICodeProps = {
  children: string;
  className: string;
}

const Code: FC<ICodeProps> = (props) => {
  const { children, className } = props;
  const language = className?.split('-')[1]
  console.log('Code', props)

  const charData = useMemo(() => {
    if (language !== 'echarts') return null;
    try {
      return JSON.parse(String(children).replace(/\n$/, ''))
    } catch (error) {
      console.error('Error parsing JSON for ECharts:', error)
      return {"title":{"text":"ECharts error - Wrong JSON format."}}
    }
  }, [language, children])

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

  if (language === 'svg') {
    return (
      <Svg
        content={children.replace(/\n/g, '')}
      />
    )
  }
  if (language === 'mermaid') {
    return (
      <MermaidChart
        content={String(children).replace(/\n$/, '')}
      />
    )
  }
  
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
  return <code className="rb:bg-[#F0F3F8] rb:px-1 rb:py-0.5 rb:rounded rb:text-sm rb:font-mono rb:whitespace-break-spaces">{children}</code>
}

export default Code
