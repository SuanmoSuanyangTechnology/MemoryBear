import { $createTextNode, $getSelection, $isRangeSelection, $setSelection, $createRangeSelection } from 'lexical';
import { INSERT_UNORDERED_LIST_COMMAND, INSERT_ORDERED_LIST_COMMAND } from '@lexical/list';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { BoldOutlined, ItalicOutlined, UnderlineOutlined, OrderedListOutlined, UnorderedListOutlined } from '@ant-design/icons';

/**
 * Rich text editor toolbar buttons
 * Toolbar buttons insert Markdown marker characters (e.g. **bold**) instead of applying Lexical format styles
 */
export const Toolbar = () => {
  const [editor] = useLexicalComposerContext();

  const insertMarkdownFormat = (prefix: string, suffix: string) => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        const selectedText = selection.getTextContent();
        if (selectedText) {
          // When text is selected, insert markers around the selected text
          const textNode = $createTextNode(`${prefix}${selectedText}${suffix}`);
          selection.insertNodes([textNode]);
        } else {
          // When no text is selected, insert only markers and move cursor to the middle
          const fullText = `${prefix}${suffix}`;
          const textNode = $createTextNode(fullText);
          selection.insertNodes([textNode]);
          // Move cursor after prefix (i.e. to the middle of the markers)
          const newSelection = $createRangeSelection();
          newSelection.anchor.set(textNode.getKey(), prefix.length, 'text');
          newSelection.focus.set(textNode.getKey(), prefix.length, 'text');
          $setSelection(newSelection);
        }
      }
    });
  };

  const insertList = (listType: 'bullet' | 'number') => {
    editor.dispatchCommand(
      listType === 'bullet' ? INSERT_UNORDERED_LIST_COMMAND : INSERT_ORDERED_LIST_COMMAND,
      undefined
    );
  };

  return (
    <div className="rb:flex rb:items-center rb:gap-1 rb:border-b rb:border-gray-200 rb:px-3 rb:py-2 rb:bg-gray-50 rb:rounded-t-lg">
      <button
        type="button"
        onClick={() => insertMarkdownFormat('**', '**')}
        className="rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200"
        title="Bold"
      >
        <BoldOutlined />
      </button>
      <button
        type="button"
        onClick={() => insertMarkdownFormat('*', '*')}
        className="rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200"
        title="Italic"
      >
        <ItalicOutlined />
      </button>
      <button
        type="button"
        onClick={() => insertMarkdownFormat('<u>', '</u>')}
        className="rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200"
        title="Underline"
      >
        <UnderlineOutlined />
      </button>
      <div className="rb:w-px rb:h-4 rb:bg-gray-300 rb:mx-1" />
      <button
        type="button"
        onClick={() => insertList('number')}
        className="rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200"
        title="Ordered List"
      >
        <OrderedListOutlined />
      </button>
      <button
        type="button"
        onClick={() => insertList('bullet')}
        className="rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200"
        title="Unordered List"
      >
        <UnorderedListOutlined />
      </button>
    </div>
  );
};

