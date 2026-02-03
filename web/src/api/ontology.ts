import { request } from '@/utils/request'
import type { Query, OntologyModalData, OntologyClassModalData, OntologyClassExtractModalData } from '@/views/Ontology/types'

// Scene list
export const getOntologyScenesUrl = '/memory/ontology/scenes'
export const getOntologyScenesList = (data: Query) => {
  return request.get(getOntologyScenesUrl, data)
}

// Create scene
export const createOntologyScene = (data: OntologyModalData) => {
  return request.post('/memory/ontology/scene', data)
}
// Update scene
export const updateOntologyScene = (scene_id: string, data: OntologyModalData) => {
  return request.put(`/memory/ontology/scene/${scene_id}`, data)
}
// Delete scene
export const deleteOntologyScene = (scene_id: string) => {
  return request.delete(`/memory/ontology/scene/${scene_id}`)
}

// Get class list
export const getOntologyclassesUrl = '/memory/ontology/classes'
export const getOntologyClassList = (data: { scene_id: string; class_name?: string; }) => {
  return request.get(getOntologyclassesUrl, data)
}
// Extract ontology types
export const extractOntologyTypes = (data: OntologyClassExtractModalData) => {
  return request.post('/memory/ontology/extract', data)
}
// Create ontology class
export const createOntologyClass = (data: OntologyClassModalData) => {
  return request.post('/memory/ontology/class', data)
}
// Delete ontology class
export const deleteOntologyClass = (class_id: string) => {
  return request.delete(`/memory/ontology/class/${class_id}`)
}
