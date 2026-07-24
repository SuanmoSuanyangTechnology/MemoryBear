import { type FC } from 'react';
import { createPortal } from 'react-dom';
import { Space, Flex } from 'antd';
import clsx from 'clsx';

import type { AutocompletePluginState } from './useAutocompletePlugin';

interface AutocompletePopupProps {
  state: AutocompletePluginState;
  /** Unique DOM id prefix for the portalled child panels. */
  childPanelIdPrefix: string;
  /** Sizing classes for the scrollable list container (differs per plugin). */
  listSizeClassName: string;
}

/**
 * Shared presentation for the '/' autocomplete popups: the main grouped list
 * plus the multi-level child panels rendered through portals.
 */
const AutocompletePopup: FC<AutocompletePopupProps> = ({ state, childPanelIdPrefix, listSizeClassName }) => {
  const {
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
  } = state;

  if (!showSuggestions || Object.keys(groupedSuggestions).length === 0) return null;

  const expandFromMain = (key: string, option: Parameters<typeof insertMention>[0]) => {
    calcChildPanelPos(key, true);
    setExpandedPath([option]);
  };

  return (
    <>
      <div
        ref={popupRef}
        data-autocomplete-popup="true"
        onMouseDown={(e) => e.preventDefault()}
        className="rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
        style={{ top: popupPosition.top, left: popupPosition.left }}
      >
        <div className={clsx('rb:overflow-y-auto', listSizeClassName)}>
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
                            if (hasChildren) expandFromMain(option.key, option);
                          }}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            if (option.disabled && !hasChildren) return;
                            if (!option.disabled) insertMention(option);
                            if (hasChildren) expandFromMain(option.key, option);
                          }}
                          onMouseEnter={() => {
                            setSelectedIndex(globalIndex);
                            setActivePanel('main');
                            setChildActiveIndex(-1);
                            if (hasChildren) expandFromMain(option.key, option);
                            else setExpandedPath([]);
                          }}
                        >
                          {option.label &&
                            <div className="rb:font-medium rb:flex-1 rb:break-all">
                              <span className="rb:text-[#155EEF]">{`{x}`}</span> {option.label}
                            </div>
                          }
                          <Space size={2} className="rb:shrink-0">
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
            id={`${childPanelIdPrefix}${parent.key}`}
            onMouseDown={(e) => e.preventDefault()}
            className={clsx('rb:overflow-y-auto rb:text-[12px] rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2', listSizeClassName)}
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
                <span className="rb:flex-1 rb:break-all">
                  {expandedPath.slice(0, index + 1).map((item, idx) => (
                    <span key={item.key}>
                      {idx > 0 && '.'}
                      {item.label}
                    </span>
                  ))}
                </span>
                <span className="rb:shrink-0">{parent.dataType}</span>
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
                  <span className="rb:font-medium rb:flex-1 rb:break-all">
                    <span className="rb:text-[#155EEF]">{`{x}`}</span> {child.label}
                  </span>
                  <Space size={2} className="rb:shrink-0">
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

export default AutocompletePopup;
