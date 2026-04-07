/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:17:31 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 21:56:00
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

import { useState, useRef, useEffect, type FC, createContext, useContext, useCallback, useMemo } from 'react'
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

/** Context for sharing form field values between form/input/button components */
const FormContext = createContext<{
  values: Record<string, any>;
  setValue: (name: string, value: any) => void;
  onSubmit?: (values: Record<string, any>) => void;
} | null>(null)

/** Stable form wrapper component — state lives in RbMarkdown, survives components object rebuilds */
const RbForm: FC<any> = ({ children, ...props }) => (
  <Form {...props}>{children}</Form>
)

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
  /** Callback when a form button is clicked, receives form field values */
  onFormSubmit?: (values: Record<string, any>) => void;
}

/** Build stable components map — form submission handled via FormContext */
const buildComponents = () => ({
  h1: ({ children, ...props }: any) => <h1 className="rb:text-2xl rb:font-bold rb:mb-2" {...props}>{children}</h1>,
  h2: ({ children, ...props }: any) => <h2 className="rb:text-xl rb:font-bold rb:mb-2" {...props}>{children}</h2>,
  h3: ({ children, ...props }: any) => <h3 className="rb:text-lg rb:font-bold rb:mb-2" {...props}>{children}</h3>,
  h4: ({ children, ...props }: any) => <h4 className="rb:text-md rb:font-bold rb:mb-2" {...props}>{children}</h4>,
  h5: ({ children, ...props }: any) => <h5 className="rb:text-sm rb:font-bold rb:mb-2" {...props}>{children}</h5>,
  h6: ({ children, ...props }: any) => <h6 className="rb:text-xs rb:font-bold rb:mb-2" {...props}>{children}</h6>,
  ul: ({ children, ...props }: any) => <ul className="rb:list-disc rb:ml-6 rb:mb-2" {...props}>{children}</ul>,
  ol: ({ children, ...props }: any) => <ol className="rb:list-decimal rb:ml-6 rb:mb-2" {...props}>{children}</ol>,  
  li: ({ children, ...props }: any) => <li className="rb:mb-1" {...props}>{children}</li>,  
  blockquote: ({ children, ...props }: any) => <blockquote className="rb:bg-[#F6F6F6] rb:rounded-lg rb:pt-2.5 rb:pb-0.5 rb:px-3 rb:mb-3 rb:mt-1" {...props}>{children}</blockquote>,
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
  table: ({ children, ...props }: any) => <div className="rb:overflow-x-auto rb:max-w-full"><table className="rb:border rb:border-[#EBEBEB] rb:mb-2" {...props}>{children}</table></div>,
  tr: ({ children, ...props }: any) => <tr className="rb:border rb:border-[#EBEBEB]" {...props}>{children}</tr>,
  th: ({ children, ...props }: any) => <th className="rb:border rb:border-[#EBEBEB] rb:px-2 rb:py-1 rb:text-left rb:font-bold" {...props}>{children}</th>,
  td: ({ children, ...props }: any) => <td className="rb:border rb:border-[#EBEBEB] rb:px-2 rb:py-1 rb:text-left" {...props}>{children}</td>,
  button: ({ children, ...props }: any) => {
    const ctx = useContext(FormContext)
    return <RbButton {...props} onClick={() => ctx?.onSubmit?.(ctx?.values ?? {})}>{[children]}</RbButton>
  },
  input: ({ children, value, ...props }: any) => {
    const ctx = useContext(FormContext)
    const handleChange = useCallback((val: any) => {
      if (props.name) ctx?.setValue(props.name, val)
    }, [ctx, props.name])
    console.log('props', props)
    switch (props.type) {
      case 'color':
        return <ColorPicker className="rb:mb-4!" defaultValue={value} {...props} onChange={handleChange} />
      case 'time':
        return <TimePicker className="rb:mb-4!" defaultValue={value} {...props} onChange={handleChange} />
      case 'date':
        return <DatePicker className="rb:mb-4!" defaultValue={value} {...props} onChange={handleChange} />
      case 'datetime':
      case 'datetime-local':
        return <DatePicker className="rb:mb-4!" defaultValue={value} showTime={true} {...props} onChange={handleChange} />
      case 'week':
        return <DatePicker className="rb:mb-4!" defaultValue={value} picker="week" {...props} onChange={handleChange} />
      case 'month':
        return <DatePicker className="rb:mb-4!" defaultValue={value} picker="month" {...props} onChange={handleChange} />
      case 'number':
        return <InputNumber className="rb:mb-4!" defaultValue={value} {...props} onChange={handleChange} />
      case 'search':
        return <Input.Search className="rb:mb-4!" defaultValue={value} {...props} onChange={(e) => handleChange(e.target.value)} />
      case 'range':
        return <Slider className="rb:mb-4!" defaultValue={value} {...props} onChange={handleChange} />
      case 'submit':
      case 'button':
        return <RbButton className="rb:mb-4!" defaultValue={value} {...props} onClick={() => ctx?.onSubmit?.(ctx?.values ?? {})}>{[props.value || children]}</RbButton>
      case 'checkbox':
        return <Checkbox className="rb:mb-4!" defaultValue={value} {...props} onChange={(e) => handleChange(e.target.checked)}>{children}</Checkbox>
      case 'password':
        return <Input.Password className="rb:mb-4!" defaultValue={value} {...props} onChange={(e) => handleChange(e.target.value)} />
      case 'radio':
        return <Radio className="rb:mb-4!" defaultValue={value} {...props} onChange={(e) => handleChange(e.target.value)}>{children}</Radio>
      case 'select': {
        const raw = props['data-options']
        const options = (typeof raw === 'string' ? JSON.parse(raw) : raw || []).map((v: string) => ({ label: v, value: v }))
        return <Select className="rb:mb-4! rb:w-full!" defaultValue={value} options={options} onChange={(val) => { if (props.name) ctx?.setValue(props.name, val) }} />
      }
      default:
        return <Input className="rb:mb-4!" defaultValue={value} {...props} onChange={(e) => handleChange(e.target.value)} />
    }
  },
  select: ({ children, ...props }: any) => {
    const ctx = useContext(FormContext)
    return <Select className="rb:mb-4! rb:w-full!" {...props} onChange={(val) => { if (props.name) ctx?.setValue(props.name, val) }}>{children}</Select>
  },
  textarea: ({ children, ...props }: any) => {
    const ctx = useContext(FormContext)
    return <Input.TextArea className="rb:mb-4!" {...props} onChange={(e) => { if (props.name) ctx?.setValue(props.name, e.target.value) }}>{children}</Input.TextArea>
  },
  form: RbForm,
  label: ({ children, ...props }: any) => {
    return <label className="rb:block rb:font-medium rb:text-[#212332] rb:mb-2" {...props}>{children}</label>
  },
  hr: (props: any) => <hr className="rb:border-t rb:border-[#EBEBEB] rb:my-3" {...props} />,
})

const RbMarkdown: FC<RbMarkdownProps> = ({
  content,
  showHtmlComments = false,
  editable = false,
  onContentChange,
  className,
  onFormSubmit,
}) => {
  const [formValues, setFormValues] = useState<Record<string, any>>({})
  const setValue = useCallback((name: string, value: any) => setFormValues(prev => ({ ...prev, [name]: value })), [])
  const formCtx = useMemo(() => ({ values: formValues, setValue, onSubmit: onFormSubmit }), [formValues, setValue, onFormSubmit])
  const components = useMemo(() => buildComponents(), [])
  const [editContent, setEditContent] = useState(content)
  const textareaRef = useRef<any>(null)

  /** Sync edit content when external content changes */
  useEffect(() => {
    setEditContent(prev => prev !== content ? content : prev)
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
    <FormContext.Provider value={formCtx}>
    <div className={`rb:relative ${className || ''}`} onKeyDown={handleKeyDown} tabIndex={0}>
      <style>{`
        .html-comment {
          color: #999;
          font-size: 0.9em;
        }
      `}</style>

      <ReactMarkdown
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
    </FormContext.Provider>
  )
}
export default RbMarkdown