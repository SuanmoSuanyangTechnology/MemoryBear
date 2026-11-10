import { useEffect, useImperativeHandle, useRef, useState, type ForwardedRef } from 'react';
import { type LexicalEditor, type EditorState } from 'lexical';
import { generateMarkdown } from './markdownExport';
import { $setMarkdownValue } from './markdownValue';
import type { RichTextEditorRef } from './index';

interface UseRichTextEditorOptions {
  value: string;
  onChange?: (value: string) => void;
  maxLength: number;
}

/** Manages Markdown backfill, controlled sync, character counting, and imperative interface. */
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
        // Calculate character count including format markers
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
      // If value equals the last internally produced value, it's from onChange callback, skip
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
      // Generate Markdown content (including format marker characters)
      const markdown = generateMarkdown();
      
      // Calculate character count including format markers
      const charCount = markdown.length;
      setCharCount(charCount);
      
      // Record the last internally produced value
      lastInternalValue.current = markdown;
      
      // If content is empty, pass empty string
      if (charCount === 0) {
        if (onChange) {
          onChange('');
        }
        return;
      }
      
      // Only pass if text length including format markers is within limit
      if (charCount <= maxLength) {
        if (onChange) {
          onChange(markdown);
        }
      }
    });
  };

  return { editorRef, charCount, handleChange };
}
