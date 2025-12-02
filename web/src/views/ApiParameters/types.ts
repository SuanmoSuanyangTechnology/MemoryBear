export interface Section {
  name: string;
  path: string;
  method: string;
  input: string;
  output: string;
  desc: string;
}
export interface Data {
  title: string;
  meta: {
    search_switch: {
      value: string;
      desc: string;
    }[];
    status_code: {
      code: string;
      desc: string;
    }[];
  }
  sections: Section[]
}