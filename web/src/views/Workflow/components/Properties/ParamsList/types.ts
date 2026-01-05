export interface ParamItem {
  name: string;
  type: string;
  desc: string;
  required: boolean
}

export interface ParamEditModalRef {
  handleOpen: (vo?: ParamItem, index?: number) => void;
}