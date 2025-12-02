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