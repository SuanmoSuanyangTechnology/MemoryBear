import { type FC } from 'react'
import SyntaxHighlighter from 'react-syntax-highlighter';
import { atelierHeathLight } from 'react-syntax-highlighter/dist/esm/styles/hljs';
import CopyBtn from './CopyBtn';


type ICodeBlockProps = {
  value: string;
  needCopy?: boolean;
  size?: 'small' | 'default';
  showLineNumbers?: boolean;
}

// enum languageType {
//   echarts = 'echarts',
//   mermaid = 'mermaid',
//   svg = 'svg',
// }

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
