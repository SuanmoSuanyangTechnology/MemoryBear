/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:16:14 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:16:14 
 */
/**
 * Svg Component
 * 
 * Renders SVG content from string using dangerouslySetInnerHTML.
 * Used for displaying SVG code blocks in markdown.
 * 
 * @component
 */

import * as React from 'react';

/** Props interface for Svg component */
interface SvgProps {
  content: string;
}

/** Component for rendering SVG content from string */
function Svg(props: SvgProps): JSX.Element {
  const { content } = props;
  
  return React.createElement(
    'div',
    {
      className: 'svg-container',
      dangerouslySetInnerHTML: { __html: content }
    }
  );
}

export default Svg;