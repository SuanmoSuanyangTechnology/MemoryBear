import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { TextNode, $createTextNode } from 'lexical';

const Jinja2HighlightPlugin = () => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerNodeTransform(TextNode, (textNode: TextNode) => {
      const text = textNode.getTextContent();
      
      if (containsJinja2Patterns(text)) {
        const parent = textNode.getParent();
        if (!parent) return;

        const tokens = tokenizeJinja2(text);
        const newNodes = tokens.map(token => {
          const newNode = $createTextNode(token.text);
          
          switch (token.type) {
            case 'number':
              newNode.setStyle('color: #005cc5; font-weight: 500;');
              break;
            case 'header-0':
            case 'header-1':
            case 'header-2':
            case 'header-3':
            case 'header-4':
            case 'header-5':
              newNode.setStyle('color: #008000');
              break;
            case 'brace-0':
              newNode.setStyle('color: #d73a49; font-family: monospace; font-weight: bold;');
              break;
            case 'brace-1':
              newNode.setStyle('color: #0366d6; font-family: monospace; font-weight: bold;');
              break;
            case 'brace-2':
              newNode.setStyle('color: #28a745; font-family: monospace; font-weight: bold;');
              break;
            case 'brace-3':
              newNode.setStyle('color: #6f42c1; font-family: monospace; font-weight: bold;');
              break;
            case 'expression-0':
            case 'expression-1':
            case 'expression-2':
            case 'expression-3':
            case 'statement-0':
            case 'statement-1':
            case 'statement-2':
            case 'statement-3':
              // Jinja2 delimiters use same color as braces
              break;
            case 'comment-0':
            case 'comment-1':
            case 'comment-2':
            case 'comment-3':
              newNode.setStyle('color: #721c24; font-family: monospace;');
              break;
            case 'variable':
              newNode.setStyle('color: #0969da; font-weight: 500;');
              break;
            case 'filter':
              newNode.setStyle('color: #8250df; font-weight: 500;');
              break;
            case 'keyword':
              newNode.setStyle('color: #cf222e; font-weight: 600;');
              break;
          }
          
          return newNode;
        });

        if (newNodes.length > 1) {
          textNode.replace(newNodes[0]);
          for (let i = 1; i < newNodes.length; i++) {
            newNodes[i - 1].insertAfter(newNodes[i]);
          }
        }
      }
    });
  }, [editor]);

  return null;
};

function containsJinja2Patterns(text: string): boolean {
  return /[{}#\d]/.test(text);
}

function tokenizeJinja2(text: string): Array<{text: string, type: string}> {
  const tokens: Array<{text: string, type: string}> = [];
  let i = 0;
  let braceLevel = 0;
  
  while (i < text.length) {
    // Check for markdown headers (at start or after whitespace)
    if (text[i] === '#' && (i === 0 || /\s/.test(text[i - 1]))) {
      let headerLevel = 0;
      let start = i;
      while (i < text.length && text[i] === '#') {
        headerLevel++;
        i++;
      }
      // Skip space after #
      if (i < text.length && text[i] === ' ') {
        i++;
      }
      // Get the rest of the header text
      while (i < text.length && text[i] !== '\n' && !/[{}]/.test(text[i])) {
        i++;
      }
      tokens.push({ text: text.slice(start, i), type: `header-${Math.min(headerLevel - 1, 5)}` });
      continue;
    }
    
    // Check for numbers
    if (/\d/.test(text[i])) {
      let start = i;
      while (i < text.length && /[\d.]/.test(text[i])) {
        i++;
      }
      tokens.push({ text: text.slice(start, i), type: 'number' });
      continue;
    }
    
    if (text[i] === '{') {
      tokens.push({ text: '{', type: `brace-${braceLevel % 4}` });
      braceLevel++;
      i++;
    } else if (text[i] === '}') {
      braceLevel = Math.max(0, braceLevel - 1);
      tokens.push({ text: '}', type: `brace-${braceLevel % 4}` });
      i++;
    } else {
      let start = i;
      while (i < text.length && text[i] !== '{' && text[i] !== '}' && 
             !(text[i] === '#' && (i === 0 || /\s/.test(text[i - 1]))) &&
             !/\d/.test(text[i])) {
        i++;
      }
      if (start < i) {
        tokens.push({ text: text.slice(start, i), type: 'text' });
      }
    }
  }
  
  return tokens;
}

export default Jinja2HighlightPlugin;