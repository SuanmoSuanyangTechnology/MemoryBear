import { Image, Input, Select, Form, Checkbox, Radio, ColorPicker, DatePicker, TimePicker, InputNumber, Slider, Button } from 'antd'
import { EditOutlined, SaveOutlined, CloseOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import RemarkGfm from 'remark-gfm'
import RemarkMath from 'remark-math'
import RemarkBreaks from 'remark-breaks'
import RehypeKatex from 'rehype-katex'
import RehypeRaw from 'rehype-raw'
import type { FC } from 'react'
import { useState, useRef, useEffect } from 'react'

import Code from './Code'
import VideoBlock from './VideoBlock'
import AudioBlock from './AudioBlock'
import Link from './Link'
import RbButton from './RbButton'

interface RbMarkdownProps {
  content: string;
  showHtmlComments?: boolean; // 是否显示 HTML 注释，默认为 false（隐藏）
  editable?: boolean; // 是否可编辑，默认为 false
  onContentChange?: (content: string) => void; // 内容变化回调
  onSave?: (content: string) => void; // 保存回调
}

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
    // 如果是 HTML 注释的 span，应用特殊样式
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
  button: ({ children, ...props }: any) => <RbButton node={{ children }}>{[children]}</RbButton>,
  table: ({ children, ...props }: any) => <table className="rb:border rb:border-[#D9D9D9] rb:mb-2" {...props}>{children}</table>,
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
  onSave,
}) => {
  const [isEditing, setIsEditing] = useState(editable) // 如果可编辑，默认进入编辑模式
  const [editContent, setEditContent] = useState(content)
  const textareaRef = useRef<any>(null)

  // 当外部 content 变化时，同步更新编辑内容
  useEffect(() => {
    setEditContent(content)
  }, [content])

  // 当editable变化时，自动切换编辑状态
  useEffect(() => {
    if (editable) {
      setIsEditing(true)
      // 延迟聚焦，确保 textarea 已渲染
      setTimeout(() => {
        textareaRef.current?.focus()
      }, 100)
    }
  }, [editable])

  // 进入编辑模式
  const handleEdit = () => {
    setIsEditing(true)
    setEditContent(content)
    // 延迟聚焦，确保 textarea 已渲染
    setTimeout(() => {
      textareaRef.current?.focus()
    }, 100)
  }

  // 保存编辑
  const handleSave = () => {
    onContentChange?.(editContent)
    onSave?.(editContent)
    if (!editable) {
      setIsEditing(false) // 只有在非强制编辑模式下才退出编辑
    }
  }

  // 取消编辑
  const handleCancel = () => {
    setEditContent(content) // 恢复原内容
    if (!editable) {
      setIsEditing(false) // 只有在非强制编辑模式下才退出编辑
    }
  }

  // 处理 textarea 内容变化
  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newContent = e.target.value
    setEditContent(newContent)
    // 实时回调内容变化
    onContentChange?.(newContent)
  }

  // 根据参数决定是否将 HTML 注释转换为可见文本
  // 使用特殊的 markdown 语法来显示注释，避免被 rehype-raw 过滤
  const processedContent = showHtmlComments
    ? (isEditing ? editContent : content).replace(/<!--([\s\S]*?)-->/g, (_match, commentContent) => {
        // 转换为带样式的文本，使用 <span class="html-comment"> 标记
        const escaped = commentContent.trim().replace(/</g, '&lt;').replace(/>/g, '&gt;')
        return `<span class="html-comment">&lt;!-- ${escaped} --&gt;</span>`
      })
    : (isEditing ? editContent : content)

  // 如果是编辑模式，显示 textarea
  if (isEditing) {
    return (
      <div className="rb:relative">
        <style>{`
          .html-comment {
            color: #999;
            font-size: 0.9em;
          }
        `}</style>
        
        {/* 编辑工具栏 - 只在非强制编辑模式下显示 */}
        {!editable && (
          <div className="rb:flex rb:justify-end rb:gap-2 rb:mb-2">
            <Button 
              type="primary" 
              size="small" 
              icon={<SaveOutlined />}
              onClick={handleSave}
            >
              保存
            </Button>
            <Button 
              size="small" 
              icon={<CloseOutlined />}
              onClick={handleCancel}
            >
              取消
            </Button>
          </div>
        )}

        {/* 编辑区域 */}
        <Input.TextArea
          ref={textareaRef}
          value={editContent}
          onChange={handleTextareaChange}
          rows={editable ? 5 : 10}
          className="rb:font-mono rb:text-sm"
          placeholder="请输入 Markdown 内容..."
          style={{ resize: 'vertical' }}
        />
      </div>
    )
  }

  // 预览模式
  return (
    <div className="rb:relative rb:group">
      <style>{`
        .html-comment {
          color: #999;
          font-size: 0.9em;
        }
      `}</style>
      
      {/* 编辑按钮 - 只在非强制编辑模式且鼠标悬停时显示 */}
      {!editable && (
        <div className="rb:absolute rb:top-0 rb:right-0 rb:opacity-0 group-hover:rb:opacity-100 rb:transition-opacity rb:z-10">
          <Button 
            type="text" 
            size="small" 
            icon={<EditOutlined />}
            onClick={handleEdit}
            className="rb:bg-white rb:shadow-sm"
          >
            编辑
          </Button>
        </div>
      )}

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