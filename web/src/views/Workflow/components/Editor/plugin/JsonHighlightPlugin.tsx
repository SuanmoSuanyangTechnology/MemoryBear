import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $getSelection, $isRangeSelection, TextNode, $createTextNode } from 'lexical';

const JsonHighlightPlugin = () => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerNodeTransform(TextNode, (textNode: TextNode) => {
      const text = textNode.getTextContent();
      
      // Check if text contains JSON-like patterns
      if (containsJsonPatterns(text)) {
        const parent = textNode.getParent();
        if (!parent) return;

        // Split text into tokens and create new nodes with appropriate classes
        const tokens = tokenizeJson(text);
        const newNodes = tokens.map(token => {
          const newNode = $createTextNode(token.text);
          
          // Set format based on token type
          switch (token.type) {
            case 'string':
              newNode.setFormat('code');
              newNode.setStyle('color: #032f62');
              break;
            case 'number':
              newNode.setFormat('code');
              newNode.setStyle('color: #005cc5');
              break;
            case 'boolean':
              newNode.setFormat('code');
              newNode.setStyle('color: #d73a49');
              break;
            case 'null':
              newNode.setFormat('code');
              newNode.setStyle('color: #6f42c1');
              break;
            case 'key':
              newNode.setFormat('code');
              newNode.setStyle('color: #22863a; font-weight: bold');
              break;
            case 'punctuation':
              newNode.setFormat('code');
              newNode.setStyle('color: #24292e');
              break;
          }
          
          return newNode;
        });

        // Replace the original text node with the new highlighted nodes
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

function containsJsonPatterns(text: string): boolean {
  // Check for JSON-like patterns
  return /[{}\[\]:,]/.test(text) || 
         /"[^"]*"/.test(text) || 
         /\b\d+(\.\d+)?\b/.test(text) || 
         /\b(true|false|null)\b/.test(text);
}

function tokenizeJson(text: string): Array<{text: string, type: string}> {
  const tokens: Array<{text: string, type: string}> = [];
  const regex = /("[^"]*")|([{}\[\]:,])|(\b\d+(?:\.\d+)?\b)|(\b(?:true|false|null)\b)|(\s+)|([^\s{}\[\]:,"]+)/g;
  
  let match;
  while ((match = regex.exec(text)) !== null) {
    const [fullMatch, string, punctuation, number, boolean, whitespace, other] = match;
    
    if (string) {
      // Check if it's a key (followed by colon)
      const afterMatch = text.slice(match.index + fullMatch.length).trim();
      if (afterMatch.startsWith(':')) {
        tokens.push({ text: fullMatch, type: 'key' });
      } else {
        tokens.push({ text: fullMatch, type: 'string' });
      }
    } else if (punctuation) {
      tokens.push({ text: fullMatch, type: 'punctuation' });
    } else if (number) {
      tokens.push({ text: fullMatch, type: 'number' });
    } else if (boolean) {
      if (fullMatch === 'null') {
        tokens.push({ text: fullMatch, type: 'null' });
      } else {
        tokens.push({ text: fullMatch, type: 'boolean' });
      }
    } else if (whitespace || other) {
      tokens.push({ text: fullMatch, type: 'text' });
    }
  }
  
  return tokens;
}

export default JsonHighlightPlugin;