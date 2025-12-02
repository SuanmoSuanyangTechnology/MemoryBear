import * as React from 'react';

interface SvgProps {
  content: string;
}

/**
 * 渲染SVG内容的组件
 */
function Svg(props: SvgProps): JSX.Element {
  const { content } = props;
  // console.log('Svg', props)
  
  return React.createElement(
    'div',
    {
      className: 'svg-container',
      dangerouslySetInnerHTML: { __html: content }
    }
  );
}

export default Svg;