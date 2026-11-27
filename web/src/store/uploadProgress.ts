/** Global upload progress state: tasks from multiple uploads accumulate continuously until the user dismisses the notification */
import { create } from 'zustand';

export type UploadTaskStatus = 'uploading' | 'success' | 'dispatch_issue' | 'failed';

export interface UploadTask {
  id: string;
  fileName: string;
  kbName: string;
  mimeType?: string;
  status: UploadTaskStatus;
  loaded: number;
  total: number;
}

interface UploadProgressState {
  tasks: UploadTask[];
  dismissed: boolean;
  expanded: boolean;
  addTasks: (tasks: UploadTask[]) => void;
  updateProgress: (id: string, loaded: number, total?: number) => void;
  finishTask: (id: string, status: UploadTaskStatus) => void;
  dismiss: () => void;
  setExpanded: (expanded: boolean) => void;
}

export const useUploadProgress = create<UploadProgressState>((set) => ({
  tasks: [],
  dismissed: false,
  expanded: true,

  addTasks: (tasks) => set((state) => ({ tasks: [...state.tasks, ...tasks], dismissed: false })),

  updateProgress: (id, loaded, total) => set((state) => ({
    tasks: state.tasks.map((task) => task.id === id
      ? {
          ...task,
          loaded: Math.max(0, loaded),
          total: total && total > 0 ? total : task.total,
        }
      : task),
  })),

  finishTask: (id, status) => set((state) => ({
    tasks: state.tasks.map((task) => task.id === id
      ? {
          ...task,
          status,
          loaded: status === 'success' || status === 'dispatch_issue' ? task.total : task.loaded,
        }
      : task),
  })),

  dismiss: () => set({ dismissed: true, tasks: [], expanded: true }),

  setExpanded: (expanded) => set({ expanded }),
}));
