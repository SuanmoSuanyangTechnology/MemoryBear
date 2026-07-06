/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-29 17:21:28 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-06-29 17:21:28 
 */
import { useState, useEffect, useRef, useCallback, useMemo, forwardRef, type FC } from 'react';
import { FixedSizeList, type ListChildComponentProps } from 'react-window';

// ========== Virtualized text list (react-window) ==========
// Renders large text files line by line with virtualization, mounting only the
// rows within the visible area to avoid attaching a huge number of DOM nodes.
const TEXT_LINE_HEIGHT = 20; // Height of each row (px), keep in sync with lineHeight
const TEXT_CHAR_WIDTH = 8.4; // Approximate width of a single monospace character (px), used to estimate horizontal scroll width

const TextVirtualList: FC<{ content: string }> = ({ content }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  // Split content into an array of lines by newline
  const lines = useMemo(() => content.split('\n'), [content]);
  // Length of the longest line, used to estimate content width for horizontal scrolling
  const maxLineLength = useMemo(
    () => lines.reduce((max, line) => (line.length > max ? line.length : max), 0),
    [lines],
  );

  // Observe container size; FixedSizeList requires explicit width and height
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Content width: the larger of the container width and the estimated longest-line width,
  // so a horizontal scrollbar appears for long lines
  const contentWidth = Math.max(size.width, Math.ceil(maxLineLength * TEXT_CHAR_WIDTH) + 32);

  const Row = useCallback(
    ({ index, style }: ListChildComponentProps) => (
      <div
        style={{
          ...style,
          width: contentWidth,
          lineHeight: `${TEXT_LINE_HEIGHT}px`,
          paddingLeft: 16,
          paddingRight: 16,
          whiteSpace: 'pre',
        }}
        className="rb:text-sm rb:text-gray-800 rb:font-mono"
      >
        {/* Use a non-breaking space for empty lines to preserve row height */}
        {lines[index] === '' ? '\u00A0' : lines[index]}
      </div>
    ),
    [lines, contentWidth],
  );

  // Custom inner element that stretches to the content width to trigger horizontal scrolling
  const innerElementType = useMemo(
    () =>
      forwardRef<HTMLDivElement, { style: React.CSSProperties }>(({ style, ...rest }, ref) => (
        <div ref={ref} style={{ ...style, width: contentWidth }} {...rest} />
      )),
    [contentWidth],
  );

  return (
    <div
      ref={containerRef}
      className="rb:w-full rb:flex-1 rb:overflow-hidden rb:bg-white rb:rounded rb:border rb:border-gray-200"
    >
      {size.height > 0 && size.width > 0 && (
        <FixedSizeList
          height={size.height}
          width={size.width}
          itemCount={lines.length}
          itemSize={TEXT_LINE_HEIGHT}
          innerElementType={innerElementType}
          overscanCount={20}
          style={{ paddingTop: 8, paddingBottom: 8 }}
        >
          {Row}
        </FixedSizeList>
      )}
    </div>
  );
};

export default TextVirtualList;
