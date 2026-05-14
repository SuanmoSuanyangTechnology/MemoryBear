/*
 * @Author: zhaoying zhaoyingyz@126.com
 * @Date: 2026-05-13 15:22:48
 * @LastEditors: zhaoying zhaoyingyz@126.com
 * @LastEditTime: 2026-05-13 15:26:44
 * @FilePath: /web/src/components/PrivateWrap.jsx
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
import { useState, useEffect } from 'react'
// 私有组件通用包裹容器
const PrivateWrap = ({ children }) => {
  const [hasPrivate, setHasPrivate] = useState(false)
  useEffect(() => {
    const checkPackage = async () => {
      try {
        await import('@redbear/memory-brick')
        setHasPrivate(true)
      } catch {
        setHasPrivate(false)
      }
    }
    checkPackage()
  }, [])
  // 无私有包返回空，不渲染
  return hasPrivate ? children : null
}
export default PrivateWrap