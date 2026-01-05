/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:45:54 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2025-12-11 13:43:52
 */
import { type ReactNode } from 'react'

/**
 * 聊天消息项接口
 */
export interface ChatItem {
  /** 消息唯一标识 */
  id?: string;
  /** 会话ID */
  conversation_id?: string | null;
  /** 消息角色：用户或助手 */
  role?: 'user' | 'assistant';
  /** 消息内容 */
  content?: string | null;
  /** 创建时间 */
  created_at?: number | string
}

/**
 * 聊天组件主要属性接口
 */
export interface ChatProps {
  /** 空状态显示内容 */
  empty?: ReactNode;
  /** 聊天数据列表 */
  data: ChatItem[];
  /** 输入内容变化回调 */
  onChange: (message: string) => void;
  /** 发送消息回调 */
  onSend: () => void;
  /** 流式加载状态 */
  streamLoading?: boolean;
  /** 加载状态 */
  loading: boolean;
  /** 内容区域自定义样式类名 */
  contentClassName?: string;
  /** 子组件内容 */
  children?: ReactNode;
  /** 标签格式化函数 */
  labelFormat: (item: ChatItem) => any;
  errorDesc?: string;
}

/**
 * 聊天输入框组件属性接口
 */
export interface ChatInputProps {
  /** 当前输入消息 */
  message?: string;
  /** 输入内容变化回调 */
  onChange: (message: string) => void;
  /** 发送消息回调 */
  onSend: () => void;
  /** 加载状态 */
  loading: boolean;
  /** 子组件内容 */
  children?: ReactNode;
}

/**
 * 聊天内容区域组件属性接口
 */
export interface ChatContentProps {
  /** 自定义样式类名 */
  classNames?: string | Record<string, boolean>;
  contentClassNames?: string | Record<string, boolean>;
  /** 聊天数据列表 */
  data: ChatItem[];
  /** 流式加载状态 */
  streamLoading: boolean;
  /** 空状态显示内容 */
  empty?: ReactNode;
  /** 标签位置：顶部或底部 */
  labelPosition?: 'top' | 'bottom';
  /** 标签格式化函数 */
  labelFormat: (item: ChatItem) => any;
  errorDesc?: string;
}