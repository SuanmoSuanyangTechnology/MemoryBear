import type { ModelListItem } from '../../types'

export interface ModelList extends ModelListItem {
  api_key_id: string;
}
export interface SubModelModalForm {
  provider: string;
  api_key_ids: string[][];
}
export interface SubModelModalRef {
  handleOpen: () => void;
}
export interface SubModelModalProps {
  type?: string;
  refresh?: (vo: ModelList[]) => void;
  groupedByProvider?: Record<string, ModelList[]>
}