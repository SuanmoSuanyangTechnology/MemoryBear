/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:17:31 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:17:31 
 */
/**
 * RbMarkdown Component
 * 
 * A comprehensive markdown renderer with support for:
 * - Standard markdown syntax (headings, lists, tables, etc.)
 * - Code syntax highlighting
 * - Math equations (KaTeX)
 * - Mermaid diagrams
 * - ECharts visualizations
 * - SVG rendering
 * - Audio/video embedding
 * - Interactive form elements
 * - HTML comments visibility toggle
 * - Editable mode with live preview
 * 
 * @component
 */

import { useState, useRef, useEffect, type FC } from 'react'
import { Image, Input, Select, Form, Checkbox, Radio, ColorPicker, DatePicker, TimePicker, InputNumber, Slider } from 'antd'
import ReactMarkdown from 'react-markdown'
import RemarkGfm from 'remark-gfm'
import RemarkMath from 'remark-math'
import RemarkBreaks from 'remark-breaks'
import RehypeKatex from 'rehype-katex'
import RehypeRaw from 'rehype-raw'

import Code from './Code'
import VideoBlock from './VideoBlock'
import AudioBlock from './AudioBlock'
import Link from './Link'
import RbButton from './RbButton'

/** Props interface for RbMarkdown component */
interface RbMarkdownProps {
  /** Markdown content to render */
  content: string;
  /** Whether to display HTML comments (default: false) */
  showHtmlComments?: boolean;
  /** Whether the content is editable (default: false) */
  editable?: boolean;
  /** Callback fired when content changes in edit mode */
  onContentChange?: (content: string) => void;
  /** Additional CSS classes */
  className?: string;
}

/** Custom component mappings for markdown elements */
const components = {
  h1: ({ children, ...props }: any) => <h1 className="rb:text-2xl rb:font-bold rb:mb-2" {...props}>{children}</h1>,
  h2: ({ children, ...props }: any) => <h2 className="rb:text-xl rb:font-bold rb:mb-2" {...props}>{children}</h2>,
  h3: ({ children, ...props }: any) => <h3 className="rb:text-lg rb:font-bold rb:mb-2" {...props}>{children}</h3>,
  h4: ({ children, ...props }: any) => <h4 className="rb:text-md rb:font-bold rb:mb-2" {...props}>{children}</h4>,
  h5: ({ children, ...props }: any) => <h5 className="rb:text-sm rb:font-bold rb:mb-2" {...props}>{children}</h5>,
  h6: ({ children, ...props }: any) => <h6 className="rb:text-xs rb:font-bold rb:mb-2" {...props}>{children}</h6>,
  ul: ({ children, ...props }: any) => <ul className="rb:list-disc rb:ml-6 rb:mb-2" {...props}>{children}</ul>,
  ol: ({ children, ...props }: any) => <ol className="rb:list-decimal rb:ml-6 rb:mb-2" {...props}>{children}</ol>,  
  li: ({ children, ...props }: any) => <li className="rb:mb-1" {...props}>{children}</li>,  
  blockquote: ({ children, ...props }: any) => <blockquote className="rb:border-l-4 rb:border-[#D9D9D9] rb:pl-4 rb:mb-2" {...props}>{children}</blockquote>,
  p: ({ children, ...props }: any) => <p className="rb:mb-2" {...props}>{children}</p>,
  strong: ({ children, ...props }: any) => <strong className="rb:font-bold" {...props}>{children}</strong>,
  em: ({ children, ...props }: any) => <em className="rb:italic" {...props}>{children}</em>,
  del: ({ children, ...props }: any) => <del className="rb:line-through" {...props}>{children}</del>,
  span: ({ children, style, ...restProps }: any) => {
    // Apply special styling for HTML comment spans
    if (style?.color === '#999') {
      return <span style={{ color: '#999', fontSize: '0.9em' }}>{children}</span>
    }
    return <span style={style} {...restProps}>{children}</span>
  },

  code: ({ children, className, ...props }: any) => <Code children={String(children)} className={className || ''} {...props} />,
  img: ({ src, alt, ...props }: any) => <Image src={src} alt={alt} {...props} />,
  video: ({ src, ...props }: any) => <VideoBlock node={{ children: [{ properties: { src: src || '' } }] }} {...props} />,
  audio: ({ src, ...props }: any) => <AudioBlock node={{ children: [{ properties: { src: src || '' } }] }} {...props} />,
  a: ({ href, children, ...props }: any) => <Link href={href || '#'} {...props}>{children}</Link>,
  button: ({ children }: any) => <RbButton node={{ children }}>{[children]}</RbButton>,
  table: ({ children, ...props }: any) => <div className="rb:overflow-x-auto rb:max-w-full"><table className="rb:border rb:border-[#D9D9D9] rb:mb-2" {...props}>{children}</table></div>,
  tr: ({ children, ...props }: any) => <tr className="rb:border rb:border-[#D9D9D9]" {...props}>{children}</tr>,
  th: ({ children, ...props }: any) => <th className="rb:border rb:border-[#D9D9D9] rb:px-2 rb:py-1 rb:text-left rb:font-bold" {...props}>{children}</th>,
  td: ({ children, ...props }: any) => <td className="rb:border rb:border-[#D9D9D9] rb:px-2 rb:py-1 rb:text-left" {...props}>{children}</td>,
  input: ({ children, ...props }: any) => {
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
        return <RbButton node={{ children: props.value || children }}>{[props.value || children]}</RbButton>
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
  select: ({ children, ...props }: any) => <Select style={{width: '100%'}} {...props}>{children}</Select>,
  textarea: ({ children, ...props }: any) => <Input.TextArea {...props}>{children}</Input.TextArea>,
  form: ({ children, ...props }: any) => <Form {...props}>{children}</Form>,
}

const RbMarkdown: FC<RbMarkdownProps> = ({
  content,
  showHtmlComments = false,
  editable = false,
  onContentChange,
  className
}) => {
  const [editContent, setEditContent] = useState(content)
  const textareaRef = useRef<any>(null)

  /** Sync edit content when external content changes */
  useEffect(() => {
    setEditContent(content)
  }, [content])

  /** Handle textarea content changes and trigger callback */
  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newContent = e.target.value
    setEditContent(newContent)
    /** Trigger real-time content change callback */
    onContentChange?.(newContent)
  }

  /**
   * Process content based on showHtmlComments flag
   * Converts HTML comments to visible text when showHtmlComments is true
   * Uses special span markup to display comments with styling
   */
  const processedContent = showHtmlComments
    ? (editable ? editContent : content).replace(/<!--([\s\S]*?)-->/g, (_match, commentContent) => {
        /** Convert to styled text using span with html-comment class */
        const escaped = commentContent.trim().replace(/</g, '&lt;').replace(/>/g, '&gt;')
        return `<span class="html-comment">&lt;!-- ${escaped} --&gt;</span>`
      })
    : (editable ? editContent : content)

  /** Render textarea in edit mode */
  if (editable) {
    return (
      <div className="rb:relative">
        <style>{`
          .html-comment {
            color: #999;
            font-size: 0.9em;
          }
        `}</style>

        {/* Edit area with textarea */}
        <Input.TextArea
          ref={textareaRef}
          value={editContent}
          onChange={handleTextareaChange}
          rows={10}
          className="rb:font-mono rb:text-sm"
          placeholder="Enter Markdown content..."
          style={{ resize: 'vertical' }}
        />
      </div>
    )
  }

  /** Handle keyboard shortcuts (e.g., Ctrl+C for copy) */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
      const selection = window.getSelection()
      if (selection && selection.toString()) {
        navigator.clipboard.writeText(selection.toString())
      }
    }
  }

  /** Render markdown preview mode */
  return (
    <div className={`rb:relative ${className || ''}`} onKeyDown={handleKeyDown} tabIndex={0}>
      <style>{`
        .html-comment {
          color: #999;
          font-size: 0.9em;
        }
      `}</style>

      <ReactMarkdown
        // allowElement={[]}
        // allowedElements={[]}
        components={components as any}
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