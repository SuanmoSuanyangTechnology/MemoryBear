export interface SpaceConfigData {
  llm: string;
  embedding: string;
  rerank: string;
}
export interface SpaceConfigRef {
  handleOpen: () => void;
}