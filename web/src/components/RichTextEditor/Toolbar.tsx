import clsx from 'clsx';
import { useEffect, useState } from 'react';
import { $getSelection, $isRangeSelection } from 'lexical';
import { INSERT_UNORDERED_LIST_COMMAND, INSERT_ORDERED_LIST_COMMAND } from '@lexical/list';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { BoldOutlined, ItalicOutlined, UnderlineOutlined, OrderedListOutlined, UnorderedListOutlined } from '@ant-design/icons';

/**
 * Rich text editor toolbar buttons
 */
export const Toolbar = () => {
  const [editor] = useLexicalComposerContext();
  const [isBold, setIsBold] = useState(false);
  const [isItalic, setIsItalic] = useState(false);
  const [isUnderline, setIsUnderline] = useState(false);

  useEffect(() => {
    const updateToolbar = () => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        setIsBold(selection.hasFormat('bold'));
        setIsItalic(selection.hasFormat('italic'));
        setIsUnderline(selection.hasFormat('underline'));
      }
    };
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(updateToolbar);
    });
  }, [editor]);

  const formatText = (format: 'bold' | 'italic' | 'underline') => {
    editor.update(() => {
      const selection = $getSelection();
      if ($isRangeSelection(selection)) {
        selection.formatText(format);
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
        onClick={() => formatText('bold')}
        className={clsx(
          "rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200",
          isBold && "rb:bg-gray-200"
        )}
        title="Bold"
      >
        <BoldOutlined />
      </button>
      <button
        type="button"
        onClick={() => formatText('italic')}
        className={clsx(
          "rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200",
          isItalic && "rb:bg-gray-200"
        )}
        title="Italic"
      >
        <ItalicOutlined />
      </button>
      <button
        type="button"
        onClick={() => formatText('underline')}
        className={clsx(
          "rb:px-1.5 rb:py-0.5 rb:text-[14px] rb:rounded rb:hover:bg-gray-200",
          isUnderline && "rb:bg-gray-200"
        )}
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

