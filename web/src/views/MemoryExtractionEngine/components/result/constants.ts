/*
 * Result module constants
 */
import { type TagProps } from '@/components/Tag'

/** Result metric mapping */
export const resultObj = {
  extractTheNumberOfEntities: 'entities.extracted_count',
  perceptualMemory: 'perceptual.count',
  memoryFragments: 'memory.chunks',
  numberOfRelationalTriples: 'triplets.count'
}

/** Tag color mapping by status */
export const tagColors: {
  [key: string]: TagProps['color']
} = {
  pending: 'warning',
  processing: 'processing',
  completed: 'success',
  failed: 'error'
}

/** Initial module state */
export const initObj = {
  data: [],
  status: 'pending',
  result: null
}

/** Default expanded state of each result card */
export const initialExpanded = {
  text_preprocessing: false,
  chunking: false,
  knowledge_extraction: false,
  creating_nodes_edges: false,
  deduplication: false,
  perceptual: false,
  dataStatistics: false,
  entityDeduplicationImpact: false,
  disambiguation: false,
  coreEntities: false,
  triplet_samples: false,
  ontologyCoverage: false,
}

/** Attachment upload reuses the conversation upload API; images and document types are allowed here */
export const chatFeatures = {
  file_upload: {
    enabled: true,
    allowed_transfer_methods: ['local_file', 'remote_url'],
    max_file_count: 5,
    image_enabled: true,
    image_max_size_mb: 20,
    image_allowed_extensions: ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'],
    audio_enabled: true,
    audio_max_size_mb: 50,
    audio_allowed_extensions: ['mp3', 'wav', 'ogg', 'aac', 'flac', 'm4a', 'wma'],
    document_enabled: true,
    document_image_recognition: true,
    document_max_size_mb: 20,
    document_allowed_extensions: ['txt', 'md', 'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx', 'csv', 'json'],
    video_enabled: true,
    video_max_size_mb: 100,
    video_allowed_extensions: ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv'],
  },
}
