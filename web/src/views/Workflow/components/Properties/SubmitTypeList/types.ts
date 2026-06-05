export interface SubmitTypeItem {
  type: string;
  enabled: boolean;
  config?: Record<string, unknown>
}

export type SubmitTypes = Record<string, SubmitTypeItem>

export interface SubmitTypeEditModalRef {
  handleOpen: (variable?: SubmitTypeItem) => void;
  handleClose: () => void;
}
