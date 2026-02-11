/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:57:30 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:57:30 
 */
/**
 * User Memory Detail Page
 * Routes to Neo4j or RAG storage implementation based on workspace configuration
 */

import { type FC } from 'react'

import { useUser } from '@/store/user'
import Neo4j from './Neo4j'
import Rag from './Rag'

const UserMemoryDetail: FC = () => {
  const { storageType } = useUser()

  if (storageType === 'neo4j') {
    return <Neo4j />
  }
  if (storageType === 'rag') {
    return <Rag />
  }
  return null
}
export default UserMemoryDetail