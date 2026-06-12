/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-02 17:10:59 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-07 14:50:14
 */
import { useEffect, useLayoutEffect, useState, useRef, type FC } from 'react';
import { createPortal } from 'react-dom';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $getSelection, $isRangeSelection, $isTextNode,
  COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND,
  KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND,
} from 'lexical';
import { Space, Flex } from 'antd';
import clsx from 'clsx';

import { CLOSE_AUTOCOMPLETE_COMMAND } from '../commands';
import type { Suggestion } from './AutocompletePlugin';

const Jinja2AutocompletePlugin: FC<{ options: Suggestion[] }> = ({ options }) => {
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0, anchorBottom: 0 });
  const [expandedPath, setExpandedPath] = useState<Suggestion[]>([]);
  const [childPanelPos, setChildPanelPos] = useState({ top: 0, horizontal: 0, useRight: true });
  const [panelPositions, setPanelPositions] = useState<Map<string, { top: number; horizontal: number; useRight: boolean }>>(new Map());
  const [activePanel, setActivePanel] = useState<'main' | 'child'>('main');
  const [childActiveIndex, setChildActiveIndex] = useState(-1);
  const popupRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const childItemRefs = useRef<Map<string, HTMLElement>>(new Map());

  const CHILD_PANEL_HEIGHT = 280;
  const CHILD_PANEL_WIDTH = 280;
  const MARGIN = 8;

  const expandedParent = expandedPath.length > 0 ? expandedPath[expandedPath.length - 1] : null;

  useLayoutEffect(() => {
    if (!popupRef.current || !showSuggestions) return;
    const { top, left, anchorBottom } = popupPosition;
    const popupHeight = popupRef.current.offsetHeight;
    const popupWidth = popupRef.current.offsetWidth;
    const MARGIN = 10;

    let finalTop: number;
    if (top - popupHeight - MARGIN >= 0) {
      finalTop = top - popupHeight - MARGIN;
    } else {
      finalTop = anchorBottom + MARGIN;
      if (finalTop + popupHeight > window.innerHeight - MARGIN)
        finalTop = window.innerHeight - popupHeight - MARGIN;
    }

    let finalLeft = left;
    if (finalLeft + popupWidth > window.innerWidth - MARGIN)
      finalLeft = window.innerWidth - popupWidth - MARGIN;
    if (finalLeft < MARGIN) finalLeft = MARGIN;

    if (finalTop !== top || finalLeft !== left)
      setPopupPosition(prev => ({ ...prev, top: finalTop, left: finalLeft }));
  }, [showSuggestions, popupPosition.anchorBottom]);

  /**
   * Compute panel position that avoids screen edges.
   * @param fromMainPanel true → anchor on a main-panel item; false → anchor on a child-panel item
   */
  const calcChildPanelPos = (key: string, fromMainPanel: boolean = false) => {
    const calculateSmartPos = (rect: DOMRect) => {
      const spaceRight = window.innerWidth - rect.left;
      const useRight = spaceRight >= CHILD_PANEL_WIDTH + MARGIN;
      const horizontal = useRight
        ? window.innerWidth - rect.left + MARGIN
        : rect.right + MARGIN;

      // Align panel top with the main popup's top so all levels share the same vertical edge
      let top: number;
      if (popupRef.current) {
        top = popupRef.current.getBoundingClientRect().top;
      } else {
        const calculatedTop = rect.bottom - CHILD_PANEL_HEIGHT;
        top = Math.max(MARGIN, calculatedTop);
      }

      return { top, horizontal, useRight };
    };

    if (fromMainPanel) {
      const el = itemRefs.current.get(key);
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const { top, horizontal, useRight } = calculateSmartPos(rect);
      setChildPanelPos({ top, horizontal, useRight });
    } else {
      const el = childItemRefs.current.get(key);
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const { top, horizontal, useRight } = calculateSmartPos(rect);
      setPanelPositions(prev => new Map(prev).set(key, { top, horizontal, useRight }));
    }
  };

  const resetState = () => {
    setShowSuggestions(false);
    setExpandedPath([]);
    setChildPanelPos({ top: 0, horizontal: 0, useRight: true });
    setPanelPositions(new Map());
    setActivePanel('main');
    setChildActiveIndex(-1);
  };

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const selection = $getSelection();
        if (!selection || !$isRangeSelection(selection)) { setShowSuggestions(false); return; }
        const anchorNode = selection.anchor.getNode();
        const anchorOffset = selection.anchor.offset;
        const textBeforeCursor = anchorNode.getTextContent().substring(0, anchorOffset);
        const shouldShow = textBeforeCursor.endsWith('/');
        setShowSuggestions(shouldShow);
        if (!shouldShow) {
          setSelectedIndex(0);
          setExpandedPath([]);
          setChildPanelPos({ top: 0, horizontal: 0, useRight: true });
          setPanelPositions(new Map());
          setActivePanel('main');
          setChildActiveIndex(-1);
          return;
        }
        const domSelection = window.getSelection();
        if (domSelection && domSelection.rangeCount > 0) {
          const rect = domSelection.getRangeAt(0).getBoundingClientRect();
          let left = rect.left;
          if (left + 280 > window.innerWidth) left = window.innerWidth - 280 - 10;
          if (left < 10) left = 10;
          setPopupPosition({ top: rect.top, left, anchorBottom: rect.bottom });
        }
      });
    });
  }, [editor]);

  useEffect(() => {
    return editor.registerCommand(
      CLOSE_AUTOCOMPLETE_COMMAND,
      () => { resetState(); return true; },
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
    document.dispatchEvent(new CustomEvent('jinja2-variable-inserted', { detail: { value: suggestion.value } }));
    resetState();
  };

  const groupedSuggestions = options.reduce((groups: Record<string, Suggestion[]>, s) => {
    const id = s.nodeData.id as string;
    if (!groups[id]) groups[id] = [];
    groups[id].push(s);
    return groups;
  }, {});

  // Flat list of main-panel items for keyboard navigation
  const flatOptions = Object.values(groupedSuggestions).flat();

  // Sync child panel position when keyboard navigates to a parent with children
  useEffect(() => {
    if (selectedIndex < 0 || selectedIndex >= flatOptions.length) return;
    const s = flatOptions[selectedIndex];
    if (s.children?.length) {
      // Defer until the ref is attached
      const timer = setTimeout(() => {
        calcChildPanelPos(s.key, true);
        setExpandedPath([s]);
      }, 0);
      return () => clearTimeout(timer);
    } else {
      setExpandedPath([]);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex]);

  // Scroll child active item into view
  useEffect(() => {
    if (!expandedParent?.children?.length || childActiveIndex < 0) return;
    const child = expandedParent.children[childActiveIndex];
    if (child) childItemRefs.current.get(child.key)?.scrollIntoView({ block: 'nearest' });
  }, [childActiveIndex, expandedParent]);

  useEffect(() => {
    if (!showSuggestions) return;
    return editor.registerCommand(
      KEY_ENTER_COMMAND,
      (event) => {
        if (!showSuggestions) return false;
        if (activePanel === 'child' && expandedParent?.children?.length) {
          const child = expandedParent.children[childActiveIndex];
          if (child && !child.disabled) { event?.preventDefault(); insertMention(child); return true; }
        } else if (flatOptions.length > 0) {
          const opt = flatOptions[selectedIndex];
          if (opt && !opt.disabled) { event?.preventDefault(); insertMention(opt); return true; }
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH,
    );
  }, [showSuggestions, selectedIndex, flatOptions, activePanel, childActiveIndex, expandedParent]);

  useEffect(() => {
    if (!showSuggestions) return;
    const down = editor.registerCommand(KEY_ARROW_DOWN_COMMAND, (e) => {
      if (!showSuggestions) return false;
      e?.preventDefault();
      if (activePanel === 'child' && expandedParent?.children) {
        setChildActiveIndex(i => {
          const newIndex = Math.min(i + 1, expandedParent.children!.length - 1);
          // Auto-expand next level when landing on a child with children
          const nextChild = expandedParent.children![newIndex];
          if (nextChild?.children?.length) {
            setTimeout(() => {
              calcChildPanelPos(nextChild.key);
              setExpandedPath(prev => [...prev, nextChild]);
            }, 0);
          } else if (expandedPath.length > 1) {
            // Otherwise collapse deeper levels
            setExpandedPath(prev => prev.slice(0, -1));
          }
          return newIndex;
        });
      } else {
        setSelectedIndex(prev => {
          let next = prev + 1;
          while (next < flatOptions.length && flatOptions[next].disabled && !flatOptions[next].children?.length) next++;
          const newIndex = next >= flatOptions.length ? prev : next;
          setTimeout(() => itemRefs.current.get(flatOptions[newIndex]?.key)?.scrollIntoView({ block: 'nearest' }), 0);
          return newIndex;
        });
      }
      return true;
    }, COMMAND_PRIORITY_HIGH);

    const up = editor.registerCommand(KEY_ARROW_UP_COMMAND, (e) => {
      if (!showSuggestions) return false;
      e?.preventDefault();
      if (activePanel === 'child' && expandedParent?.children) {
        setChildActiveIndex(i => {
          const newIndex = Math.max(i - 1, 0);
          const nextChild = expandedParent.children![newIndex];
          if (nextChild?.children?.length) {
            setTimeout(() => {
              calcChildPanelPos(nextChild.key);
              setExpandedPath(prev => [...prev, nextChild]);
            }, 0);
          } else if (expandedPath.length > 1) {
            setExpandedPath(prev => prev.slice(0, -1));
          }
          return newIndex;
        });
      } else {
        setSelectedIndex(prev => {
          let p = prev - 1;
          while (p >= 0 && flatOptions[p].disabled && !flatOptions[p].children?.length) p--;
          const newIndex = p < 0 ? prev : p;
          setTimeout(() => itemRefs.current.get(flatOptions[newIndex]?.key)?.scrollIntoView({ block: 'nearest' }), 0);
          return newIndex;
        });
      }
      return true;
    }, COMMAND_PRIORITY_HIGH);

    const esc = editor.registerCommand(KEY_ESCAPE_COMMAND, (e) => {
      e?.preventDefault(); setShowSuggestions(false); return true;
    }, COMMAND_PRIORITY_HIGH);

    return () => { down(); up(); esc(); };
  }, [showSuggestions, selectedIndex, flatOptions, editor, activePanel, childActiveIndex, expandedParent, expandedPath]);

  // ArrowLeft/Right for multi-level panel switching via native keydown (lexical doesn't expose these commands)
  useEffect(() => {
    if (!showSuggestions) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        if (activePanel === 'main') {
          // Enter deepest child panel
          const current = flatOptions[selectedIndex];
          if (current?.children?.length) {
            e.preventDefault();
            setActivePanel('child');
            setChildActiveIndex(0);
          }
        } else {
          // Drill one level deeper
          const deepest = expandedPath[expandedPath.length - 1];
          const currentChild = deepest?.children?.[childActiveIndex];
          if (currentChild?.children?.length) {
            e.preventDefault();
            setTimeout(() => {
              calcChildPanelPos(currentChild.key);
              setExpandedPath(prev => [...prev, currentChild]);
              setChildActiveIndex(0);
            }, 0);
          }
        }
      } else if (e.key === 'ArrowRight') {
        if (activePanel === 'child') {
          // Collapse deepest level; return to main when nothing more to collapse
          if (expandedPath.length > 1) {
            e.preventDefault();
            setExpandedPath(prev => prev.slice(0, -1));
            setChildActiveIndex(0);
          } else {
            e.preventDefault();
            setActivePanel('main');
            setChildActiveIndex(-1);
          }
        }
      }
    };
    document.addEventListener('keydown', handler, true);
    return () => document.removeEventListener('keydown', handler, true);
  }, [showSuggestions, activePanel, selectedIndex, flatOptions, expandedPath, childActiveIndex]);

  if (!showSuggestions || Object.keys(groupedSuggestions).length === 0) return null;

  return (
    <>
      <div
        ref={popupRef}
        data-autocomplete-popup="true"
        onMouseDown={(e) => e.preventDefault()}
        className="rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
        style={{ top: popupPosition.top, left: popupPosition.left }}
      >
        <div className="rb:min-w-70 rb:max-h-57.5 rb:overflow-y-auto">
          <Flex vertical gap={12}>
            {Object.entries(groupedSuggestions).map(([nodeId, nodeOptions]) => {
              const nodeName = nodeOptions[0]?.nodeData?.name || nodeId;
              return (
                <div key={nodeId} className="rb:text-[12px]">
                  {nodeName !== 'undefined' &&
                    <div className="rb:px-2 rb:leading-4.25 rb:mb-1.25 rb:font-medium rb:text-[#5B6167]">
                      {nodeName}
                    </div>
                  }
                  <Flex vertical gap={2}>
                    {nodeOptions.map((option) => {
                      const globalIndex = flatOptions.indexOf(option);
                      const hasChildren = !!option.children?.length;
                      const isExpanded = expandedParent?.key === option.key;
                      const isActive = activePanel === 'main' && selectedIndex === globalIndex;
                      return (
                        <Flex
                          key={option.key}
                          ref={(el) => { if (el) itemRefs.current.set(option.key, el); }}
                          className={clsx('rb:px-2! rb:py-0.75! rb:rounded-sm rb:leading-4.5 rb:text-[#5B6167] rb:hover:bg-[#F6F6F6]', {
                            'rb:bg-[#F6F6F6]': isActive || isExpanded,
                            'rb:cursor-not-allowed rb:opacity-65': option.disabled && !hasChildren,
                            'rb:cursor-pointer': !option.disabled || hasChildren,
                          })}
                          align="center"
                          justify="space-between"
                          onClick={() => {
                            if (option.disabled && !hasChildren) return;
                            if (!option.disabled) insertMention(option);
                            if (hasChildren) {
                              calcChildPanelPos(option.key, true);
                              setExpandedPath([option]);
                            }
                          }}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            if (option.disabled && !hasChildren) return;
                            if (!option.disabled) insertMention(option);
                            if (hasChildren) {
                              calcChildPanelPos(option.key, true);
                              setExpandedPath([option]);
                            }
                          }}
                          onMouseEnter={() => {
                            setSelectedIndex(globalIndex);
                            setActivePanel('main');
                            setChildActiveIndex(-1);
                            if (hasChildren) {
                              calcChildPanelPos(option.key, true);
                              setExpandedPath([option]);
                            } else {
                              setExpandedPath([]);
                            }
                          }}
                        >
                          {option.label &&
                            <div className="rb:font-medium">
                              <span className="rb:text-[#155EEF]">{`{x}`}</span> {option.label}
                            </div>
                          }
                          <Space size={2}>
                            {option.dataType && <span>{option.dataType}</span>}
                            {hasChildren && <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:rotate-90"></div>}
                          </Space>
                        </Flex>
                      );
                    })}
                  </Flex>
                </div>
              );
            })}
          </Flex>
        </div>
      </div>

      {/* Child variables panels - one per level in expandedPath, fixed positioned via portal to avoid clipping */}
      {expandedPath.length > 0 && expandedPath.map((parent, index) => {
        const position = index === 0 ? childPanelPos : panelPositions.get(parent.key);
        if (!position) return null;
        return createPortal(
          <div
            key={parent.key}
            id={`jinja2-autocomplete-child-panel-${parent.key}`}
            onMouseDown={(e) => e.preventDefault()}
            className="rb:min-w-70 rb:max-h-57.5 rb:overflow-y-auto rb:text-[12px] rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
            style={{
              top: position.top,
              ...(position.useRight
                ? { right: position.horizontal }
                : { left: position.horizontal })
            }}
            onMouseEnter={() => {
              setActivePanel('child');
              if (childActiveIndex < 0) setChildActiveIndex(0);
            }}
          >
            <div className="rb:pb-2 rb:mb-1 rb:font-medium rb:text-[#5B6167] rb-border-b">
              <Flex justify="space-between" align="center" gap={8}>
                <span>
                  {expandedPath.slice(0, index + 1).map((item, idx) => (
                    <span key={item.key}>
                      {idx > 0 && '.'}
                      {item.label}
                    </span>
                  ))}
                </span>
                <span>{parent.dataType}</span>
              </Flex>
            </div>
            {parent.children?.map((child, ci) => {
              const hasChildren = !!child.children?.length;
              const isChildActive = activePanel === 'child' && expandedPath.length - 1 === index && ci === childActiveIndex;
              return (
                <Flex
                  key={child.key}
                  ref={(el) => { if (el) childItemRefs.current.set(child.key, el); }}
                  className={clsx('rb:px-2! rb:py-0.75! rb:rounded-sm rb:leading-4.5 rb:text-[#5B6167] rb:hover:bg-[#F6F6F6]', {
                    'rb:bg-[#F6F6F6]': isChildActive,
                    'rb:cursor-not-allowed rb:opacity-65': child.disabled,
                    'rb:cursor-pointer': !child.disabled,
                  })}
                  align="center"
                  justify="space-between"
                  onClick={() => !child.disabled && insertMention(child)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    if (!child.disabled) insertMention(child);
                  }}
                  onMouseEnter={() => {
                    if (child.disabled) return;
                    setActivePanel('child');
                    setChildActiveIndex(ci);
                    if (hasChildren) {
                      // Defer until the ref is attached
                      const timer = setTimeout(() => {
                        calcChildPanelPos(child.key);
                        setExpandedPath(prev => [...prev.slice(0, index + 1), child]);
                      }, 0);
                      return () => clearTimeout(timer);
                    } else {
                      // No children: collapse any deeper level panels
                      setExpandedPath(prev => prev.slice(0, index + 1));
                    }
                  }}
                >
                  <span className="rb:font-medium">
                    <span className="rb:text-[#155EEF]">{`{x}`}</span> {child.label}
                  </span>
                  <Space size={2}>
                    {child.dataType && <span>{child.dataType}</span>}
                    {hasChildren && <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:rotate-90"></div>}
                  </Space>
                </Flex>
              );
            })}
          </div>,
          document.body
        );
      })}
    </>
  );
};

export default Jinja2AutocompletePlugin;
