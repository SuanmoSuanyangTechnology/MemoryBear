/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:16:01 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:16:01 
 */
/**
 * MermaidChart Component
 * 
 * Renders Mermaid diagrams as images.
 * - Converts Mermaid syntax to SVG
 * - Converts SVG to base64 data URL for display
 * - Generates unique IDs based on content hash
 * 
 * @component
 */

import { useRef, useEffect, useState, type FC } from 'react'
import mermaid from 'mermaid'
import CryptoJS from 'crypto-js'
import { Image } from 'antd'

/** Initialize Mermaid with default configuration */
mermaid.initialize({
  startOnLoad: true,
  theme: 'default',
  flowchart: {
    htmlLabels: true,
    useMaxWidth: true,
  },
})

/** Convert SVG string to base64 data URL for image display */
const svgToBase64 = (svgGraph: string) => {
  const svgBytes = new TextEncoder().encode(svgGraph)
  const blob = new Blob([svgBytes], { type: 'image/svg+xml;charset=utf-8' })
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onloadend = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

/** Mermaid chart component that renders Mermaid diagrams as images */
const MermaidChart: FC<{ content: string }> = ({ content }) => {
  const [chartSvg, setChartSvg] = useState<string>('')
  /** Generate unique ID based on content hash to avoid conflicts */
  const id = useRef(`mermaidchart_${CryptoJS.MD5(content).toString()}`)

  useEffect(() => {
    if (!content || content === '') {
      return
    }
    drawDiagram()
  }, [content])

  /** Render Mermaid diagram and convert to base64 image */
  const drawDiagram = async function () {
    const { svg } = await mermaid.render(id.current, content);

    const base64 = await svgToBase64(svg)
    setChartSvg(base64 as string)
  };
  return (
    <Image src={chartSvg} />
  )
}
export default MermaidChart