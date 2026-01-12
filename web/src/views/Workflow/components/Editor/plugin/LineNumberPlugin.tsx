import { useEffect, useState } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot } from 'lexical';

const LineNumberPlugin = () => {
  const [editor] = useLexicalComposerContext();
  const [lineCount, setLineCount] = useState(1);

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        const paragraphCount = root.getChildren().length;
        const lines = Math.max(1, paragraphCount);
        setLineCount(lines);
      });
    });
  }, [editor]);

  useEffect(() => {
    const updateLineNumbers = () => {
      const lineNumbersElement = document.querySelector('.line-numbers');
      const editorElement = document.querySelector('.editor-content-with-numbers');
      
      if (lineNumbersElement && editorElement) {
        const paragraphs = editorElement.querySelectorAll('p');
        
        // Clear existing line numbers
        lineNumbersElement.innerHTML = '';
        
        // Create line numbers positioned at each paragraph
        paragraphs.forEach((paragraph, index) => {
          const lineNumber = document.createElement('div');
          lineNumber.textContent = (index + 1).toString();
          lineNumber.style.position = 'absolute';
          lineNumber.style.top = paragraph.offsetTop + 'px';
          lineNumber.style.right = '8px';
          lineNumber.style.height = '20px';
          lineNumber.style.lineHeight = '20px';
          lineNumbersElement.appendChild(lineNumber);
        });
        
        // Set line numbers container to relative positioning
        (lineNumbersElement as HTMLElement).style.position = 'relative';
      }
    };

    // Update line numbers after content changes
    const timer = setTimeout(updateLineNumbers, 100);
    
    // Also update on window resize
    window.addEventListener('resize', updateLineNumbers);
    
    return () => {
      clearTimeout(timer);
      window.removeEventListener('resize', updateLineNumbers);
    };
  }, [lineCount]);

  return null;
};

export default LineNumberPlugin;