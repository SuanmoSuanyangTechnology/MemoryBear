/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-19 17:11:30 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-19 17:11:30 
 */
import { useEffect, useState, useRef, useMemo, type FC } from 'react';
import { createPortal } from 'react-dom';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $createTextNode, $setSelection, $createRangeSelection, $isTextNode, $getSelection, $isRangeSelection, COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND, KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND } from 'lexical';
import { Flex } from 'antd';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import { INSERT_OPTION_COMMAND, CLOSE_AUTOCOMPLETE_COMMAND, type OptionItem } from '../commands';

interface AutocompletePluginProps {
  options: OptionItem[];
}


const AutocompletePlugin: FC<AutocompletePluginProps> = ({ options }) => {
  const { t } = useTranslation();
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0 });
  const popupRef = useRef<HTMLDivElement>(null);

  const filteredOptions = options.map((option, index) => ({
    ...option,
    key: option.key || `option-${index}`
  }));

  const ADD_NEW_VARIABLE_OPTION: OptionItem = useMemo(() => ({
    key: '__add_new_variable__',
    label: t('application.addNewVariable'),
    value: ''
  }), [t]);
  const allOptions = useMemo(
    () => [ADD_NEW_VARIABLE_OPTION, ...filteredOptions],
    [ADD_NEW_VARIABLE_OPTION, filteredOptions]
  );

  const resetState = () => {
    setShowSuggestions(false);
    setSelectedIndex(0);
  };

  useEffect(() => {
    const editorElement = editor.getRootElement();
    const handleBlur = (e: FocusEvent) => {
      const relatedTarget = e.relatedTarget as Node | null;
      if (relatedTarget && editorElement?.contains(relatedTarget)) {
        return;
      }
      setShowSuggestions(false);
    };
    
    editorElement?.addEventListener('blur', handleBlur, true);
    return () => {
      editorElement?.removeEventListener('blur', handleBlur, true);
    };
  }, [editor]);

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const selection = $getSelection();
        
        if (!selection || !$isRangeSelection(selection)) {
          setShowSuggestions(false);
          return;
        }

        const anchorNode = selection.anchor.getNode();
        const anchorOffset = selection.anchor.offset;
        const nodeText = anchorNode.getTextContent();
        const textBeforeCursor = nodeText.substring(0, anchorOffset);
        const shouldShow = textBeforeCursor.endsWith('/') || 
                          (textBeforeCursor === '/' && anchorOffset === 1);
        
        setShowSuggestions(shouldShow);
        if (!shouldShow) {
          setSelectedIndex(0);
        }

        if (shouldShow) {
          const domSelection = window.getSelection();
          if (domSelection && domSelection.rangeCount > 0) {
            const range = domSelection.getRangeAt(0);
            const rect = range.getBoundingClientRect();
            setPopupPosition({ top: rect.bottom + 4, left: rect.left });
          }
        }
      });
    });
  }, [editor]);

  useEffect(() => {
    return editor.registerCommand(
      CLOSE_AUTOCOMPLETE_COMMAND,
      () => {
        resetState();
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );
  }, [editor]);

  const insertOption = (option: OptionItem) => {
    editor.dispatchCommand(INSERT_OPTION_COMMAND, { data: option });
    resetState();
  };

  const insertNewVariable = () => {
    editor.update(() => {
      const selection = $getSelection();
      if (!selection || !$isRangeSelection(selection)) return;
      
      const anchorNode = selection.anchor.getNode();
      const anchorOffset = selection.anchor.offset;
      
      if ($isTextNode(anchorNode)) {
        const nodeText = anchorNode.getTextContent();
        const textBeforeCursor = nodeText.substring(0, anchorOffset);
        const textAfterCursor = nodeText.substring(anchorOffset);
        
        const lastSlashIndex = textBeforeCursor.lastIndexOf('/');
        
        if (lastSlashIndex !== -1) {
          const beforeSlash = textBeforeCursor.substring(0, lastSlashIndex);
          
          anchorNode.setTextContent(beforeSlash);
          
          const openBrace = $createTextNode('{{');
          const closeBrace = $createTextNode('}}');
          
          anchorNode.insertAfter(openBrace);
          openBrace.insertAfter(closeBrace);
          
          if (textAfterCursor) {
            closeBrace.insertAfter($createTextNode(textAfterCursor));
          }
          
          const newSelection = $createRangeSelection();
          newSelection.anchor.set(openBrace.getKey(), 2, 'text');
          newSelection.focus.set(openBrace.getKey(), 2, 'text');
          $setSelection(newSelection);
        }
      }
    });
    resetState();
  };

  useEffect(() => {
    if (!showSuggestions) return;

    return editor.registerCommand(
      KEY_ENTER_COMMAND,
      (event) => {
        if (!showSuggestions) return false;
        if (allOptions.length > 0) {
          const selectedOption = allOptions[selectedIndex];
          if (selectedOption) {
            event?.preventDefault();
            if (selectedOption.key === ADD_NEW_VARIABLE_OPTION.key) {
              insertNewVariable();
            } else {
              insertOption(selectedOption);
            }
            return true;
          }
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH
    );
  }, [showSuggestions, selectedIndex, allOptions, insertOption, editor]);

  useEffect(() => {
    if (!showSuggestions) return;

    const unregisterArrowDown = editor.registerCommand(
      KEY_ARROW_DOWN_COMMAND,
      (event) => {
        if (!showSuggestions) return false;
        event?.preventDefault();
        setSelectedIndex(prev => {
          const next = prev + 1;
          return next >= allOptions.length ? prev : next;
        });
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );

    const unregisterArrowUp = editor.registerCommand(
      KEY_ARROW_UP_COMMAND,
      (event) => {
        if (!showSuggestions) return false;
        event?.preventDefault();
        setSelectedIndex(prev => Math.max(prev - 1, 0));
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );

    const unregisterEscape = editor.registerCommand(
      KEY_ESCAPE_COMMAND,
      (event) => {
        if (showSuggestions) {
          event?.preventDefault();
          setShowSuggestions(false);
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH
    );

    return () => {
      unregisterArrowDown();
      unregisterArrowUp();
      unregisterEscape();
    };
  }, [showSuggestions, allOptions.length, editor]);

  useEffect(() => {
    return editor.registerCommand(
      INSERT_OPTION_COMMAND,
      (payload) => {
        const { data } = payload;
        editor.update(() => {
          const selection = $getSelection();
          if (!selection || !$isRangeSelection(selection)) return;
          
          const anchorNode = selection.anchor.getNode();
          const anchorOffset = selection.anchor.offset;
          
          if ($isTextNode(anchorNode)) {
            const nodeText = anchorNode.getTextContent();
            const textBeforeCursor = nodeText.substring(0, anchorOffset);
            const textAfterCursor = nodeText.substring(anchorOffset);
            
            const lastSlashIndex = textBeforeCursor.lastIndexOf('/');
            
            if (lastSlashIndex !== -1) {
              const beforeSlash = textBeforeCursor.substring(0, lastSlashIndex);
              
              anchorNode.setTextContent(beforeSlash);
              
              const textNode = $createTextNode(data.value);
              
              anchorNode.insertAfter(textNode);
              
              if (textAfterCursor) {
                textNode.insertAfter($createTextNode(textAfterCursor));
              }
              
              const newSelection = $createRangeSelection();
              newSelection.anchor.set(textNode.getKey(), data.value.length, 'text');
              newSelection.focus.set(textNode.getKey(), data.value.length, 'text');
              $setSelection(newSelection);
            }
          }
        });
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );
  }, [editor]);

  if (!showSuggestions) return null;
  if (allOptions.length === 0) return null;

  return createPortal(
    <div
      ref={popupRef}
      data-autocomplete-popup="true"
      onMouseDown={(e) => e.preventDefault()}
      className="rb:fixed rb:z-50 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
      style={{
        top: popupPosition.top,
        left: popupPosition.left,
        minWidth: '200px',
      }}
    >
      <div className="rb:max-h-57.5 rb:overflow-y-auto">
        <Flex vertical gap={3}>
          {allOptions.map((option, index) => {
            const isAddNewVariable = option.key === ADD_NEW_VARIABLE_OPTION.key;
            return (
              <Flex
                key={option.key}
                className={clsx(
                  "rb:px-2! rb:py-0.75! rb:rounded-sm rb:text-[14px] rb:leading-4.5 rb:cursor-pointer rb:transition-colors",
                  {
                    'rb:bg-[#F6F6F6]': selectedIndex === index,
                    'rb:hover:bg-[#F6F6F6]': selectedIndex !== index,
                  }
                )}
                align="center"
                justify="space-between"
                onClick={() => {
                  if (isAddNewVariable) {
                    insertNewVariable();
                  } else {
                    insertOption(option);
                  }
                }}
                onMouseEnter={() => setSelectedIndex(index)}
              >
                {isAddNewVariable ? (
                  <span className="rb:font-medium rb:text-[#155EEF]">+ {ADD_NEW_VARIABLE_OPTION.label}</span>
                ) : (
                  <>
                    <span className="rb:font-medium">{option.label}</span>
                    <span className="rb:text-[#8C8C8C] rb:text-[12px]">{option.value}</span>
                  </>
                )}
              </Flex>
            );
          })}
        </Flex>
      </div>
    </div>,
    document.body
  );
};

export default AutocompletePlugin;
