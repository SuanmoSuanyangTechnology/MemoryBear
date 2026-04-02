/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-02 17:10:59 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-02 17:10:59 
 */
import { useEffect, useState, useRef, type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $getSelection, $isRangeSelection, $isTextNode,
  COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND,
  KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND,
} from 'lexical';
import { Space, Flex } from 'antd';

import { CLOSE_AUTOCOMPLETE_COMMAND } from '../commands';
import type { Suggestion } from './AutocompletePlugin';

const Jinja2AutocompletePlugin: FC<{ options: Suggestion[] }> = ({ options }) => {
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0 });
  const popupRef = useRef<HTMLDivElement>(null);

  const scrollSelectedIntoView = () => {
    if (!popupRef.current) return;
    const selectedElement = popupRef.current.querySelector('[data-selected="true"]');
    if (!selectedElement) return;
    const container = popupRef.current;
    const element = selectedElement as HTMLElement;
    const containerRect = container.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    if (elementRect.bottom > containerRect.bottom) {
      container.scrollTop += elementRect.bottom - containerRect.bottom;
    } else if (elementRect.top < containerRect.top) {
      container.scrollTop -= containerRect.top - elementRect.top;
    }
  };

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
        const textBeforeCursor = anchorNode.getTextContent().substring(0, anchorOffset);
        const shouldShow = textBeforeCursor.endsWith('/');
        setShowSuggestions(shouldShow);
        if (!shouldShow) { setSelectedIndex(0); return; }

        const domSelection = window.getSelection();
        if (domSelection && domSelection.rangeCount > 0) {
          const rect = domSelection.getRangeAt(0).getBoundingClientRect();
          const popupWidth = 280, popupHeight = 200;
          const vw = window.innerWidth, vh = window.innerHeight;
          let left = Math.min(Math.max(rect.left, 10), vw - popupWidth - 10);
          let top = rect.top - 10;
          if (top - popupHeight < 10) {
            top = Math.min(rect.bottom + 10, vh - popupHeight - 10);
          }
          setPopupPosition({ top, left });
        }
      });
    });
  }, [editor]);

  useEffect(() => {
    return editor.registerCommand(
      CLOSE_AUTOCOMPLETE_COMMAND,
      () => { setShowSuggestions(false); return true; },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor]);

  const insertMention = (suggestion: Suggestion) => {
    editor.update(() => {
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return;
      const anchorNode = selection.anchor.getNode();
      const anchorOffset = selection.anchor.offset;
      const nodeText = anchorNode.getTextContent();
      const textBefore = nodeText.substring(0, anchorOffset - 1);
      const textAfter = nodeText.substring(anchorOffset);
      const inserted = `{{${suggestion.value}}}`;
      if ($isTextNode(anchorNode)) {
        anchorNode.setTextContent(textBefore + inserted + textAfter);
        const newOffset = textBefore.length + inserted.length;
        selection.anchor.offset = newOffset;
        selection.focus.offset = newOffset;
      }
    });
    setShowSuggestions(false);
  };

  const groupedSuggestions = options.reduce((groups: Record<string, Suggestion[]>, s) => {
    const id = s.nodeData.id as string;
    if (!groups[id]) groups[id] = [];
    groups[id].push(s);
    return groups;
  }, {});

  const allOptions = Object.values(groupedSuggestions).flat();

  useEffect(() => {
    if (!showSuggestions) return;
    return editor.registerCommand(
      KEY_ENTER_COMMAND,
      (event) => {
        const opt = allOptions[selectedIndex];
        if (opt && !opt.disabled) { event?.preventDefault(); insertMention(opt); return true; }
        return false;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [showSuggestions, selectedIndex, allOptions]);

  useEffect(() => {
    if (!showSuggestions) return;
    const down = editor.registerCommand(KEY_ARROW_DOWN_COMMAND, (e) => {
      e?.preventDefault();
      setSelectedIndex(prev => {
        let next = prev + 1;
        while (next < allOptions.length && allOptions[next].disabled) next++;
        setTimeout(scrollSelectedIntoView, 0);
        return next >= allOptions.length ? prev : next;
      });
      return true;
    }, COMMAND_PRIORITY_HIGH);
    const up = editor.registerCommand(KEY_ARROW_UP_COMMAND, (e) => {
      e?.preventDefault();
      setSelectedIndex(prev => {
        let p = prev - 1;
        while (p >= 0 && allOptions[p].disabled) p--;
        setTimeout(scrollSelectedIntoView, 0);
        return p < 0 ? prev : p;
      });
      return true;
    }, COMMAND_PRIORITY_HIGH);
    const esc = editor.registerCommand(KEY_ESCAPE_COMMAND, (e) => {
      e?.preventDefault(); setShowSuggestions(false); return true;
    }, COMMAND_PRIORITY_HIGH);
    return () => { down(); up(); esc(); };
  }, [showSuggestions, selectedIndex, allOptions, editor]);

  if (!showSuggestions || Object.keys(groupedSuggestions).length === 0) return null;

  return (
    <div
      ref={popupRef}
      data-autocomplete-popup="true"
      onMouseDown={(e) => e.preventDefault()}
      className="rb:fixed rb:z-1000 rb:py-1 rb:bg-white rb:rounded-xl rb:min-w-70 rb:max-h-50 rb:overflow-y-auto rb:transform-[translateY(-100%)] rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]"
      style={{ top: popupPosition.top, left: popupPosition.left }}
    >
      <Flex vertical gap={12}>
        {Object.entries(groupedSuggestions).map(([nodeId, nodeOptions]) => (
          <div key={nodeId}>
            <Flex align="center" gap={4} className="rb:px-3! rb:text-[12px] rb:py-1.25! rb:font-medium rb:text-[#5B6167]">
              {nodeOptions[0]?.nodeData?.icon && <img src={nodeOptions[0].nodeData.icon} className="rb:size-3" alt="" />}
              {nodeOptions[0]?.nodeData?.name || nodeId}
            </Flex>
            {nodeOptions.map((option) => {
              const globalIndex = allOptions.indexOf(option);
              return (
                <Flex
                  key={option.key}
                  data-selected={selectedIndex === globalIndex}
                  className="rb:pl-6! rb:pr-3! rb:py-2!"
                  align="center"
                  justify="space-between"
                  style={{
                    cursor: option.disabled ? 'not-allowed' : 'pointer',
                    background: selectedIndex === globalIndex ? '#f0f8ff' : 'white',
                    opacity: option.disabled ? 0.5 : 1,
                  }}
                  onClick={() => !option.disabled && insertMention(option)}
                  onMouseEnter={() => setSelectedIndex(globalIndex)}
                >
                  <Space size={4}>
                    <span className="rb:text-[#155EEF]">{option.isContext ? '📄' : '{x}'}</span>
                    <span>{option.label}</span>
                  </Space>
                  {option.dataType && <span className="rb:text-[#5B6167]">{option.dataType}</span>}
                </Flex>
              );
            })}
          </div>
        ))}
      </Flex>
    </div>
  );
};

export default Jinja2AutocompletePlugin;
