import { $createParagraphNode, $createTextNode, type LexicalNode } from 'lexical';
import { ListNode, ListItemNode } from '@lexical/list';

const LIST_RULES = [
  { type: 'number', pattern: /^\d+\.\s+(.+)$/ },
  { type: 'bullet', pattern: /^[-*]\s+(.+)$/ },
] as const;

// Parse Markdown text into Lexical nodes
export const parseMarkdownToNodes = (markdown: string): LexicalNode[] => {
  const lines = markdown.split('\n');
  const nodes: LexicalNode[] = [];
  
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    
    // Skip empty lines
    if (!line) {
      i++;
      continue;
    }
    
    // Both list types share grouping, inline parsing, and node creation flow.
    const listRule = LIST_RULES.find(rule => rule.pattern.test(line));
    if (listRule) {
      const listNode = new ListNode(listRule.type, 1);
      while (i < lines.length) {
        const match = lines[i].trim().match(listRule.pattern);
        if (!match) {
          break;
        }
        const listItem = new ListItemNode();
        // Insert text directly without parsing inline formatting
        listItem.append($createTextNode(match[1]));
        listNode.append(listItem);
        i++;
      }
      nodes.push(listNode);
      continue;
    }
    
    // Regular paragraph, insert text directly (preserve Markdown marker characters)
    const paragraph = $createParagraphNode();
    paragraph.append($createTextNode(line));
    nodes.push(paragraph);
    i++;
  }
  
  return nodes;
};

