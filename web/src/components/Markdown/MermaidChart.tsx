import { useRef, useEffect, useState, type FC } from 'react'
import mermaid from 'mermaid'
import CryptoJS from 'crypto-js'
import { Image } from 'antd'

mermaid.initialize({
  startOnLoad: true,
  theme: 'default',
  flowchart: {
    htmlLabels: true,
    useMaxWidth: true,
  },
})

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
const MermaidChart: FC<{ content: string }> = ({ content }) => {
  const [chartSvg, setChartSvg] = useState<string>('')
  const id = useRef(`mermaidchart_${CryptoJS.MD5(content).toString()}`)

  useEffect(() => {
    if (!content || content === '') {
      return
    }
    drawDiagram()
  }, [content])

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