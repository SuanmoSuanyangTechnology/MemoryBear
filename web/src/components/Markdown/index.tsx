/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:17:31 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-13 14:39:57
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
import RemarkRescueImages from './remarkRescueImages'
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
  /** Whether to show copy button (default: true) */
  isNeedCopy?: boolean;
}

/** Build stable components map — form submission handled via FormContext */
const buildComponents = (isNeedCopy = true) => ({
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
      return <span className="rb:text-[#999] rb:text-[0.9em]">{children}</span>
    }
    return <span style={style} {...restProps}>{children}</span>
  },

  code: ({ children, className, ...props }: any) => <Code children={String(children)} isNeedCopy={isNeedCopy ?? true} className={className || ''} {...props} />,
  img: ({ src, alt, ...props }: any) => <Image src={src} alt={alt} width={200} {...props} />,
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
  textarea: ({ children, default_value, ...props }: any) => {
    const ctx = useContext(FormContext)
    return <Input.TextArea className="rb:mb-4!" defaultValue={default_value} {...props} onChange={(e) => { if (props.name) ctx?.setValue(props.name, e.target.value) }}>{children}</Input.TextArea>
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
  isNeedCopy = true,
}) => {
  const [formValues, setFormValues] = useState<Record<string, any>>({})
  const setValue = useCallback((name: string, value: any) => setFormValues(prev => ({ ...prev, [name]: value })), [])
  const formCtx = useMemo(() => ({ values: formValues, setValue, onSubmit: onFormSubmit }), [formValues, setValue, onFormSubmit])
  const components = useMemo(() => buildComponents(isNeedCopy), [isNeedCopy])
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

  const rescueHtmlEscapedImageChars = (text: string): string => {
    if (!text) return text;
    // Only touch the exact delimiters used by the Markdown image grammar plus
    // quotes inside the url-title position.  Deliberately NOT unescaping
    // `<`, `>`, `/`, `{`, `}`, etc. — those are not required for image
    // syntax and decoding them could expand raw HTML unexpectedly.
    return text
      .replace(/&#91;|&#x5B;/gi, '[')
      .replace(/&#93;|&#x5D;/gi, ']')
      .replace(/&#40;|&#x28;/gi, '(')
      .replace(/&#41;|&#x29;/gi, ')')
      .replace(/&#33;|&#x21;/gi, '!')
      .replace(/&quot;|&#34;/gi, '"')
      .replace(/&#39;/gi, "'")
      .replace(/&apos;/gi, "'")
      // Strip backslash escapes of these six chars ONLY when the `\` itself
      // isn't already escaped (otherwise `\\!` would incorrectly become `\`
      // + a bare `!` instead of literal `\!` text).
      .replace(/(^|[^\\])\\([!()[\]])/g, '$1$2')
      ;
  };

  const deindentHtmlTagLines = (text: string): string => {
    if (!text) return text;
    const lines = text.split(/\r?\n/);
    const out: string[] = new Array(lines.length);
    let fenced: string | null = null; // null = outside; else the fence opener string e.g. "```"

    // 1. First pass: collect runs of "HTML-tag lines" outside fenced blocks,
    //    grouped with their unindented siblings (<table>/</table> lines at
    //    col 0 must share a run with indented <thead>/<tr> lines that follow).
    type Run = { start: number; end: number; minIndent: number };
    const runs: Run[] = [];
    let cur: Run | null = null;

    const isHtmlTagLine = (trimmed: string): boolean => {
      // Must begin with `<` and then satisfy one of the HTML lexical shapes.
      if (trimmed.length === 0 || trimmed.charCodeAt(0) !== 60 /* < */) return false;
      return (
        // Comment:    <!--  ....  -->
        /^<!--/.test(trimmed) ||
        // Doctype / CDATA / XML PI: <!DOCTYPE  <![CDATA[  <?xml ... ?>
        /^<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION|\[CDATA\[)/i.test(trimmed) ||
        /^<\?[A-Za-z_]/.test(trimmed) ||
        // Closing tag: </table>   </div class="ignored"> etc.
        /^<\/[A-Za-z][\w:-]*(?:\s|>)/.test(trimmed) ||
        // Opening / void tag: <td ...>  <br />  <img src=x>
        /^<[A-Za-z][\w:-]*(?:\s[^>]*|[^A-Za-z0-9\s]*?)?\/?>/.test(trimmed)
      );
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      // --- fenced-code tracking ---
      // Matches exactly the start-of-line indent-zero-or-less fence rule used
      // by GitHub / CommonMark (3+ backticks, 3+ tildes, at column 0..3, with
      // optional info-string after the fence).
      const fenceMatch = /^( {0,3})(`{3,}|~{3,})/.exec(line);
      if (fenceMatch) {
        const fence = fenceMatch[2][0]; // '`' or '~'
        const fenceLen = fenceMatch[2].length;
        if (!fenced) {
          fenced = fence.repeat(fenceLen);
          // Close any currently open HTML run at the fence boundary.
          if (cur) { runs.push(cur); cur = null; }
          out[i] = line;
          continue;
        } else if (fenced.charCodeAt(0) === fence.charCodeAt(0)) {
          // Only close when the closing fence uses >= the same number of
          // fence chars as the opener, per CommonMark spec.
          const infoRest = line.slice(fenceMatch[1].length + fenceLen);
          const validClose = /^\s*$/.test(infoRest);
          if (validClose) {
            fenced = null;
            out[i] = line;
            continue;
          }
        }
      }
      if (fenced) {
        // Inside a fenced code block — write verbatim, never a candidate run.
        if (cur) { runs.push(cur); cur = null; }
        out[i] = line;
        continue;
      }

      const trimmed = line.trimStart();
      const indent = line.length - trimmed.length;
      const htmlTag = isHtmlTagLine(trimmed);

      if (htmlTag) {
        if (!cur) {
          cur = { start: i, end: i + 1, minIndent: indent > 0 ? indent : Number.POSITIVE_INFINITY };
        } else {
          cur.end = i + 1;
          if (indent > 0 && indent < cur.minIndent) cur.minIndent = indent;
        }
      } else {
        if (cur) { runs.push(cur); cur = null; }
      }
      out[i] = line; // placeholder; second pass writes de-indented lines
    }
    if (cur) runs.push(cur);

    // 2. Second pass: in each identified run, strip the run's smallest
    //    positive leading whitespace from every indented HTML-tag line.
    //    Zero-indent wrapper tags stay unchanged and do not suppress
    //    normalization of their nested tags. Non-HTML lines in the same index
    //    range were NOT candidates, so they don't participate.
    for (const run of runs) {
      if (!Number.isFinite(run.minIndent)) continue;
      for (let i = run.start; i < run.end; i++) {
        const orig = lines[i];
        const trimmed = orig.trimStart();
        if (!isHtmlTagLine(trimmed)) continue; // blank lines / text keep indent
        out[i] = orig.slice(Math.min(run.minIndent, orig.length - trimmed.length));
      }
    }

    // Lines never touched by either pass (fenced interiors, non-HTML lines
    // outside any run) are already populated by the first pass assignments
    // above.  Fallback safety copy for any index still undefined just in
    // case the logic above missed a branch.
    for (let i = 0; i < lines.length; i++) {
      if (out[i] === undefined) out[i] = lines[i];
    }

    return out.join('\n');
  };

  /**
   * Ensure standalone HTML table tags form a separate Markdown block.
   * Markdown parsers can otherwise merge adjacent Markdown text with raw HTML
   * when the caller omits the blank line before or after the table.
   * Fenced code blocks are left untouched so code samples remain verbatim.
   */
  const addTableBlockBoundaries = (text: string): string => {
    const lines = text.split('\n');
    const result: string[] = [];
    let fenced: string | null = null;

    const appendLine = (line: string, nextLine: string | undefined) => {
      const standaloneTableStart = /^\s*<table(?:\s[^>]*)?>\s*$/i.test(line);
      const standaloneTableEnd = /^\s*<\/table>\s*$/i.test(line);
      if (standaloneTableStart && result.length > 0 && result[result.length - 1].trim() !== '') {
        result.push('');
      }

      result.push(line);

      if (standaloneTableEnd && nextLine !== undefined && nextLine.trim() !== '') {
        result.push('');
      }
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fenceMatch = /^( {0,3})(`{3,}|~{3,})/.exec(line);
      if (fenceMatch) {
        const fence = fenceMatch[2][0];
        const fenceLength = fenceMatch[2].length;
        const infoRest = line.slice(fenceMatch[1].length + fenceLength);
        if (!fenced) {
          fenced = fence.repeat(fenceLength);
        } else if (fenced[0] === fence && /^\s*$/.test(infoRest)) {
          fenced = null;
        }
        result.push(line);
        continue;
      }

      if (fenced) {
        result.push(line);
        continue;
      }

      // A compact table may contain its opening and closing tags on one line.
      // Split it into its own block so following Markdown (for example
      // `**注：**`) is parsed outside the raw HTML table.
      const tableParts = line.replace(/(<table\b[^>]*>[\s\S]*?<\/table>)/gi, '\n$1\n').split('\n');
      for (let partIndex = 0; partIndex < tableParts.length; partIndex++) {
        const nextPart = tableParts[partIndex + 1] ?? lines[i + 1];
        appendLine(tableParts[partIndex], nextPart);
      }
    }

    return result.join('\n');
  };

  const rawContent = showHtmlComments
    ? (editable ? editContent : content).replace(/<!--([\s\S]*?)-->/g, (_match, commentContent) => {
        /** Convert to styled text using span with html-comment class */
        const escaped = commentContent.trim().replace(/</g, '&lt;').replace(/>/g, '&gt;')
        return `<span class="html-comment">&lt;!-- ${escaped} --&gt;</span>`
      })
    : (editable ? editContent : content)
  const processedContent = rescueHtmlEscapedImageChars(addTableBlockBoundaries(deindentHtmlTagLines(rawContent)))

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
          className="rb:font-mono rb:text-sm rb:resize-y"
          placeholder="Enter Markdown content..."
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
        remarkPlugins={[[RemarkGfm, { singleTilde: false }], RemarkMath, RemarkBreaks, RemarkRescueImages]}
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