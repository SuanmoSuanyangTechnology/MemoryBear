import { Image, Input, Select, Form, Checkbox, Radio, ColorPicker, DatePicker, TimePicker, InputNumber, Slider } from 'antd'
import ReactMarkdown from 'react-markdown'
import RemarkGfm from 'remark-gfm'
import RemarkMath from 'remark-math'
import RemarkBreaks from 'remark-breaks'
import RehypeKatex from 'rehype-katex'
import RehypeRaw from 'rehype-raw'
import type { FC } from 'react'

import Code from './Code'
import VideoBlock from './VideoBlock'
import AudioBlock from './AudioBlock'
import Link from './Link'
import RbButton from './RbButton'

interface RbMarkdownProps {
  content: string;
  showHtmlComments?: boolean; // 是否显示 HTML 注释，默认为 false（隐藏）
}

const components = {
  h1: ({ children }: { children: string }) => <h1 className="rb:text-2xl rb:font-bold rb:mb-2">{children}</h1>,
  h2: ({ children }: { children: string }) => <h2 className="rb:text-xl rb:font-bold rb:mb-2">{children}</h2>,
  h3: ({ children }: { children: string }) => <h3 className="rb:text-lg rb:font-bold rb:mb-2">{children}</h3>,
  h4: ({ children }: { children: string }) => <h4 className="rb:text-md rb:font-bold rb:mb-2">{children}</h4>,
  h5: ({ children }: { children: string }) => <h5 className="rb:text-sm rb:font-bold rb:mb-2">{children}</h5>,
  h6: ({ children }: { children: string }) => <h6 className="rb:text-xs rb:font-bold rb:mb-2">{children}</h6>,
  ul: ({ children }: { children: string }) => <ul className="rb:list-disc rb:ml-6 rb:mb-2">{children}</ul>,
  ol: ({ children }: { children: string }) => <ol className="rb:list-decimal rb:ml-6 rb:mb-2">{children}</ol>,  
  li: ({ children }: { children: string }) => <li className="rb:mb-1">{children}</li>,  
  blockquote: ({ children }: { children: string }) => <blockquote className="rb:border-l-4 rb:border-[#D9D9D9] rb:pl-4 rb:mb-2">{children}</blockquote>,
  p: ({ children }: { children: string }) => <p className="rb:mb-2">{children}</p>,
  strong: ({ children }: { children: string }) => <strong className="rb:font-bold">{children}</strong>,
  em: ({ children }: { children: string }) => <em className="rb:italic">{children}</em>,
  del: ({ children }: { children: string }) => <del className="rb:line-through">{children}</del>,
  span: ({ children, ...props }: any) => {
    // 如果是 HTML 注释的 span，应用特殊样式
    if (props.style?.color === '#999') {
      return <span style={{ color: '#999', fontSize: '0.9em' }}>{children}</span>
    }
    return <span {...props}>{children}</span>
  },

  code: Code,
  img: Image,
  video: VideoBlock,
  audio: AudioBlock,
  a: Link,
  button: RbButton,
  table: ({ children }: { children: string }) => <table className="rb:border rb:border-[#D9D9D9] rb:mb-2">{children}</table>,
  tr: ({ children }: { children: string }) => <tr className="rb:border rb:border-[#D9D9D9]">{children}</tr>,
  th: ({ children }: { children: string }) => <th className="rb:border rb:border-[#D9D9D9] rb:px-2 rb:py-1 rb:text-left rb:font-bold">{children}</th>,
  td: ({ children }: { children: string }) => <td className="rb:border rb:border-[#D9D9D9] rb:px-2 rb:py-1 rb:text-left">{children}</td>,
  input: ({ children, ...props }: { children: string }) => {
    switch (props.type) {
      case 'color':
        return <ColorPicker {...props} />
      case 'time':
        return <TimePicker {...props} />
      case 'date':
        return <DatePicker {...props} />
      case 'datetime':
      case 'datetime-local':
        return <DatePicker showTime={true} {...props} />
      case 'week':
        return <DatePicker picker="week" {...props} />
      case 'month':
        return <DatePicker picker="month" {...props} />
      case 'number':
        return <InputNumber {...props} />
      case 'search':
        return <Input.Search {...props} />
      case 'range':
        return <Slider {...props} />
      case 'submit':
      case 'button':
        return <RbButton {...props}>{props.value}</RbButton>
      case 'checkbox':
        return <Checkbox {...props}>{children}</Checkbox>
      case 'password':
        return <Input.Password {...props} />
      case 'radio':
        return <Radio {...props}>{children}</Radio>
      default:
        return <Input value={children} {...props} />
    }
  },
  select: ({ children, ...props }: { children: string }) => <Select style={{width: '100%'}} {...props}>{children}</Select>,
  textarea: ({ children, ...props }: { children: string }) => <Input.TextArea {...props}>{children}</Input.TextArea>,
  form: ({ children }: { children: string }) => <Form>{children}</Form>,
}

const RbMarkdown: FC<RbMarkdownProps> = ({
  content,
  showHtmlComments = false,
}) => {
  // 根据参数决定是否将 HTML 注释转换为可见文本
  // 使用特殊的 markdown 语法来显示注释，避免被 rehype-raw 过滤
  const processedContent = showHtmlComments
    ? content.replace(/<!--([\s\S]*?)-->/g, (_match, commentContent) => {
        // 转换为带样式的文本，使用 <span class="html-comment"> 标记
        const escaped = commentContent.trim().replace(/</g, '&lt;').replace(/>/g, '&gt;')
        return `<span class="html-comment">&lt;!-- ${escaped} --&gt;</span>`
      })
    : content

  return (
    <div>
      <style>{`
        .html-comment {
          color: #999;
          font-size: 0.9em;
        }
      `}</style>
      <ReactMarkdown
        // allowElement={[]}
        // allowedElements={[]}
        components={components}
        disallowedElements={['script', 'iframe', 'head', 'html', 'meta', 'link', 'style', 'body']}
        rehypePlugins={[
          RehypeKatex,
          RehypeRaw,
          // The Rehype plug-in is used to remove the ref attribute of an element
          // () => {
          //   return (tree) => {
          //     const iterate = (node: any) => {
          //       if (node.type === 'element' && !node.properties?.src && node.properties?.ref && node.properties.ref.startsWith('{') && node.properties.ref.endsWith('}'))
          //         delete node.properties.ref

          //       if (node.children)
          //         node.children.forEach(iterate)
          //     }
          //     tree.children.forEach(iterate)
          //   }
          // },
        ]}
        remarkPlugins={[RemarkGfm, RemarkMath, RemarkBreaks]}
        remarkRehypeOptions={{
          allowDangerousHtml: true,
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  )
}
export default RbMarkdown