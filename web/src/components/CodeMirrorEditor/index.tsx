/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-04 17:20:52 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-04 17:20:52 
 */
import { useEffect, useRef, useMemo } from 'react';
import { EditorView, basicSetup } from 'codemirror';
import { EditorState } from '@codemirror/state';
import { python } from '@codemirror/lang-python';
import { javascript } from '@codemirror/lang-javascript';
import { java } from '@codemirror/lang-java';
import { cpp } from '@codemirror/lang-cpp';
import { rust } from '@codemirror/lang-rust';
import { oneDark } from '@codemirror/theme-one-dark';

/**
 * Props for the CodeMirrorEditor component
 * @property {string} value - The initial code content to display in the editor
 * @property {string} language - Programming language for syntax highlighting (python, python3, javascript, typescript, java, cpp, c, rust)
 * @property {function} onChange - Callback function triggered when editor content changes, receives the new code value
 * @property {string} theme - Editor theme, either 'light' or 'dark'
 * @property {boolean} readOnly - Whether the editor is read-only
 * @property {string} height - Custom height for the editor
 * @property {string} size - Predefined size preset: 'default' (120px min-height, 14px font) or 'small' (60px min-height, 12px font)
 */
interface CodeMirrorEditorProps {
  value?: string;
  language?: 'python' | 'python3' | 'javascript' | 'typescript' | 'java' | 'cpp' | 'c' | 'rust';
  onChange?: (value: string) => void;
  theme?: 'light' | 'dark';
  readOnly?: boolean;
  height?: string;
  size?: 'default' | 'small';
}

/**
 * Map of language identifiers to their corresponding CodeMirror language extensions
 * Supports multiple programming languages with syntax highlighting
 */
const languageExtensions: Record<string, any> = {
  python: python(),
  python3: python(),
  javascript: javascript(),
  typescript: javascript({ typescript: true }),
  java: java(),
  cpp: cpp(),
  c: cpp(),
  rust: rust(),
};

/**
 * CodeMirrorEditor - A React wrapper component for CodeMirror 6 editor
 * Provides a code editor with syntax highlighting, theme support, and customizable sizing
 * Used in workflow code execution nodes for editing Python and JavaScript code
 */
const CodeMirrorEditor = ({
  value = '',
  language = 'javascript',
  onChange,
  theme = 'light',
  readOnly = false,
  size,
}: CodeMirrorEditorProps) => {
  // Reference to the DOM element that will contain the editor
  const editorRef = useRef<HTMLDivElement>(null);
  // Reference to the CodeMirror EditorView instance
  const viewRef = useRef<EditorView | null>(null);

  /**
   * Initialize CodeMirror editor when component mounts or when language/theme/readOnly changes
   * Sets up extensions for syntax highlighting, change listeners, and theme
   */
  useEffect(() => {
    if (!editorRef.current) return;

    // Get the appropriate language extension, fallback to JavaScript if not found
    const langExtension = languageExtensions[language] || languageExtensions.javascript;
    
    // Configure editor extensions
    const extensions = [
      basicSetup, // Basic editor features (line numbers, bracket matching, etc.)
      langExtension, // Language-specific syntax highlighting
      // Listen for document changes and trigger onChange callback
      EditorView.updateListener.of((update) => {
        if (update.docChanged && onChange) {
          onChange(update.state.doc.toString());
        }
      }),
      EditorState.readOnly.of(readOnly), // Set read-only mode
    ];

    // Apply dark theme if specified
    if (theme === 'dark') {
      extensions.push(oneDark);
    }

    // Create editor state with initial value and extensions
    const state = EditorState.create({
      doc: value,
      extensions,
    });

    // Create and mount the editor view
    viewRef.current = new EditorView({
      state,
      parent: editorRef.current,
    });

    // Cleanup: destroy editor instance when component unmounts or dependencies change
    return () => {
      viewRef.current?.destroy();
    };
  }, [language, theme, readOnly]);

  /**
   * Update editor content when the value prop changes externally
   * Only updates if the new value differs from current editor content
   */
  useEffect(() => {
    if (viewRef.current && value !== viewRef.current.state.doc.toString()) {
      viewRef.current.dispatch({
        changes: {
          from: 0,
          to: viewRef.current.state.doc.length,
          insert: value,
        },
      });
    }
  }, [value]);

  // Calculate minimum height based on size prop: small (60px) or default (120px)
  const minHeight = useMemo(() => {
    return `${size === 'small' ? 60 : 120}px`
  }, [size])
  
  // Calculate font size based on size prop: small (12px) or default (14px)
  const fontSize = useMemo(() => {
    return `${size === 'small' ? 12 : 14}px`
  }, [size])
  
  // Calculate line height based on size prop: small (16px) or default (20px)
  const lineHeight = useMemo(() => {
    return `${size === 'small' ? 16 : 20}px`
  }, [size])

  return <div ref={editorRef} style={{ minHeight, fontSize, lineHeight }} />;
};

export default CodeMirrorEditor;
