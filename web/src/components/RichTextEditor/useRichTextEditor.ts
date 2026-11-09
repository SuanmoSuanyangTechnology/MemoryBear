import { useEffect, useImperativeHandle, useRef, useState, type ForwardedRef } from 'react';
import { $getRoot, type LexicalEditor, type EditorState } from 'lexical';
import { generateMarkdown } from './markdownExport';
import { $setMarkdownValue } from './markdownValue';
import type { RichTextEditorRef } from './index';

interface UseRichTextEditorOptions {
  value: string;
  onChange?: (value: string) => void;
  maxLength: number;
}

/** 管理 Markdown 回填、受控同步、字符计数和命令式接口。 */
export function useRichTextEditor(
  { value, onChange, maxLength }: UseRichTextEditorOptions,
  ref: ForwardedRef<RichTextEditorRef>,
) {
  const editorRef = useRef<LexicalEditor | null>(null);
  const [charCount, setCharCount] = useState(value?.length || 0);
  const [isInitialized, setIsInitialized] = useState(false);
  const lastInternalValue = useRef('');


  useImperativeHandle(ref, () => ({
    clear: () => {
      if (editorRef.current) {
        editorRef.current.update(() => {
          $setMarkdownValue('');
        });
        setCharCount(0);
        if (onChange) {
          onChange('');
        }
      }
    },
    getValue: () => {
      if (editorRef.current) {
        let markdown = '';
        editorRef.current.getEditorState().read(() => {
          markdown = generateMarkdown();
        });
        return markdown;
      }
      return '';
    },
    setValue: (newValue: string) => {
      if (editorRef.current) {
        editorRef.current.update(() => {
          $setMarkdownValue(newValue);
        });
        setCharCount(newValue.length);
        if (onChange) {
          onChange(newValue);
        }
      }
    },
  }));

  // Handle initial value
  useEffect(() => {
    if (editorRef.current && !isInitialized) {
      editorRef.current.update(() => {
        $setMarkdownValue(value);
      });
      setIsInitialized(true);
      setCharCount(value?.length || 0);
      lastInternalValue.current = value;
    }
  }, [value, isInitialized]);

  // Handle value changes from form (skip if value is from internal onChange)
  useEffect(() => {
    const editor = editorRef.current;
    if (editor && isInitialized) {
      // 如果 value 等于最后一次内部产生的值，说明是 onChange 回调产生的，跳过
      if (value === lastInternalValue.current) {
        return;
      }
      
      editor.getEditorState().read(() => {
        const currentMarkdown = generateMarkdown();
        if (currentMarkdown !== value) {
          editor.update(() => {
            $setMarkdownValue(value);
          });
          setCharCount(value?.length || 0);
        }
      });
    }
  }, [value, isInitialized]);

  const handleChange = (editorState: EditorState) => {
    editorState.read(() => {
      // 获取纯文本内容用于字符计数
      const root = $getRoot();
      const textContent = root.getTextContent();
      
      // 计算纯文本字符数（不包括 Markdown 标记）
      const cleanedContent = textContent.replace(/\n/g, '').trim();
      const charCount = cleanedContent.length;
      setCharCount(charCount);
      
      // 生成 Markdown 内容用于保存
      const markdown = generateMarkdown();
      
      // 记录最后一次内部产生的值
      lastInternalValue.current = markdown;
      
      // 如果内容为空，传递空字符串
      if (charCount === 0) {
        if (onChange) {
          onChange('');
        }
        return;
      }
      
      // 只有纯文本长度在限制内才传递
      if (charCount <= maxLength) {
        if (onChange) {
          onChange(markdown);
        }
      }
    });
  };

  return { editorRef, charCount, handleChange };
}
