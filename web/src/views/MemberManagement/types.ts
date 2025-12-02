// 用户数据类型
export interface Member {
  id: string;
  username: string;
  account: string;
  role: string;
  last_login_at: string | number;
}
// 用户表单数据类型
export interface MemberModalData {
  email: string;
  role: string;
}
// 定义组件暴露的方法接口
export interface MemberModalRef {
  handleOpen: (user?: Member | null) => void;
}