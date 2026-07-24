import { useEffect, useLayoutEffect, useState, useRef } from 'react';
import type { LexicalEditor } from 'lexical';
import {
  $getSelection, $isRangeSelection,
  COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND,
  KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND,
} from 'lexical';

import { CLOSE_AUTOCOMPLETE_COMMAND } from '../../commands';
import type { Suggestion, PanelPos } from './types';
import { calcSmartPanelPos } from './smartPos';

interface UseAutocompletePluginParams {
  editor: LexicalEditor;
  options: Suggestion[];
  /** Decide whether the popup should be shown for the given text before the cursor. */
  getShouldShow: (textBeforeCursor: string, anchorOffset: number) => boolean;
  /** Editor-specific insertion of the picked suggestion (state reset is handled here). */
  doInsert: (suggestion: Suggestion) => void;
}

/**
 * Shared behaviour for the '/' triggered variable autocomplete popups.
 * Encapsulates positioning, grouping, multi-level child panels and keyboard
 * navigation so the concrete plugins only supply their trigger + insert logic.
 */
export function useAutocompletePlugin({
  editor,
  options,
  getShouldShow,
  doInsert,
}: UseAutocompletePluginParams) {
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0, anchorBottom: 0 });
  const [expandedPath, setExpandedPath] = useState<Suggestion[]>([]);
  const [childPanelPos, setChildPanelPos] = useState<PanelPos>({ top: 0, horizontal: 0, useRight: true });
  const [panelPositions, setPanelPositions] = useState<Map<string, PanelPos>>(new Map());
  const [activePanel, setActivePanel] = useState<'main' | 'child'>('main');
  const [childActiveIndex, setChildActiveIndex] = useState(-1);
  const popupRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const childItemRefs = useRef<Map<string, HTMLElement>>(new Map());

  const expandedParent = expandedPath.length > 0 ? expandedPath[expandedPath.length - 1] : null;

  // Adjust popup position after render based on actual size
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
    const anchorTop = popupRef.current ? popupRef.current.getBoundingClientRect().top : null;
    if (fromMainPanel) {
      const el = itemRefs.current.get(key);
      if (!el) return;
      setChildPanelPos(calcSmartPanelPos(el.getBoundingClientRect(), anchorTop));
    } else {
      const el = childItemRefs.current.get(key);
      if (!el) return;
      setPanelPositions(prev => new Map(prev).set(key, calcSmartPanelPos(el.getBoundingClientRect(), anchorTop)));
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

  // Insert the selected suggestion then reset the popup state
  const insertMention = (suggestion: Suggestion) => {
    doInsert(suggestion);
    resetState();
  };

  // Listen to editor updates and show suggestions when the trigger matches
  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const selection = $getSelection();
        if (!selection || !$isRangeSelection(selection)) { setShowSuggestions(false); return; }
        const anchorNode = selection.anchor.getNode();
        const anchorOffset = selection.anchor.offset;
        const textBeforeCursor = anchorNode.getTextContent().substring(0, anchorOffset);
        const shouldShow = getShouldShow(textBeforeCursor, anchorOffset);
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

  // Register command to close autocomplete popup
  useEffect(() => {
    return editor.registerCommand(
      CLOSE_AUTOCOMPLETE_COMMAND,
      () => { resetState(); return true; },
      COMMAND_PRIORITY_HIGH,
    );
  }, [editor]);

  // Group suggestions by node ID
  const groupedSuggestions = options.reduce((groups: Record<string, Suggestion[]>, suggestion) => {
    const id = suggestion.nodeData?.id as string;
    if (!groups[id]) groups[id] = [];
    groups[id].push(suggestion);
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
  }, [selectedIndex]);

  // Scroll child active item into view
  useEffect(() => {
    if (!expandedParent?.children?.length || childActiveIndex < 0) return;
    const child = expandedParent.children[childActiveIndex];
    if (child) childItemRefs.current.get(child.key)?.scrollIntoView({ block: 'nearest' });
  }, [childActiveIndex, expandedParent]);

  // Handle Enter key to select suggestion
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

  // Handle keyboard navigation (Arrow Up/Down, Escape)
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

  return {
    showSuggestions,
    popupRef,
    popupPosition,
    groupedSuggestions,
    flatOptions,
    selectedIndex, setSelectedIndex,
    expandedPath, setExpandedPath,
    expandedParent,
    childPanelPos,
    panelPositions,
    activePanel, setActivePanel,
    childActiveIndex, setChildActiveIndex,
    itemRefs,
    childItemRefs,
    calcChildPanelPos,
    insertMention,
  };
}

export type AutocompletePluginState = ReturnType<typeof useAutocompletePlugin>;
