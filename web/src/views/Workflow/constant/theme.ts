/* 主题色板与便签/未知节点配置（拆分自 constant.ts） */
export const THEME_MAP: Record<string, { outer: string; title: string; bg: string; border: string }> = {
  blue: {
    outer: '#2E90FA',
    title: '#D1E9FF',
    bg: '#EFF8FF',
    border: '#84CAFF',
  },
  cyan: {
    outer: '#06AED4',
    title: '#CFF9FE',
    bg: '#ECFDFF',
    border: '#67E3F9',
  },
  green: {
    outer: '#16B364',
    title: '#D3F8DF',
    bg: '#EDFCF2',
    border: '#73E2A3',
  },
  yellow: {
    outer: '#EAAA08',
    title: '#FEF7C3',
    bg: '#FEFBE8',
    border: '#FDE272',
  },
  pink: {
    outer: '#EE46BC',
    title: '#FCE7F6',
    bg: '#FDF2FA',
    border: '#FAA7E0',
  },
  violet: {
    outer: '#875BF7',
    title: '#ECE9FE',
    bg: '#F5F3FF',
    border: '#C3B5FD',
  },
}

export const notesConfig = {
  type: "notes",
  icon: 'rb:bg-[url("@/assets/images/workflow/unknown.svg")]',
  config: {
    text: {
      type: 'define',
    },
    theme: {
      type: 'define',
      defaultValue: 'blue',
    },
    width: {
      type: 'define',
      width: 240,
    },
    height: {
      type: 'define',
      height: 120,
    },
    author: {
      type: 'define',
    },
    show_author: {
      type: 'define',
      defaultValue: true
    }
  }
}
export const unknownNode = {
  type: 'unknown',
  icon: 'rb:bg-[url("@/assets/images/workflow/unknown.svg")]'
}
export const noteNode = {
  type: 'notes',
  icon: 'rb:bg-[url("@/assets/images/workflow/unknown.svg")]'
}
