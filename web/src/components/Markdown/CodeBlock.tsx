import { type FC } from 'react'
import SyntaxHighlighter from 'react-syntax-highlighter';
import { atelierHeathLight } from 'react-syntax-highlighter/dist/esm/styles/hljs';
import CopyBtn from './CopyBtn';


type ICodeBlockProps = {
  value: string;
}

// enum languageType {
//   echarts = 'echarts',
//   mermaid = 'mermaid',
//   svg = 'svg',
// }

const CodeBlock: FC<ICodeBlockProps> = ({
  value,
}) => {

  return (
    <div className="rb:relative">
      <SyntaxHighlighter
        style={atelierHeathLight}
        customStyle={{
          padding: '16px 20px 16px 24px',
          backgroundColor: '#F0F3F8',
          borderRadius: 8,
        }}
        language="json"
        showLineNumbers={false}
        PreTag="div"
      >
        {value}
      </SyntaxHighlighter>
      <CopyBtn
        value={value}
        style={{
          position: 'absolute',
          top: 20,
          right: 20,
        }}
      />
    </div>
  )
}

export default CodeBlock
