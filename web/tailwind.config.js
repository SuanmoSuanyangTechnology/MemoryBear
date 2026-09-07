/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/**/*.{js,ts,jsx,tsx}",
    "./src/**/*.{js,ts,jsx,tsx}",
    "./src/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        gray: {
          50: '#FAFAFA',
          100: '#F6F6F6',
          200: '#EBEBEB',
          300: '#E3E5E8',
          400: '#DFE4ED',
          500: '#A8A9AA',
          600: '#5B6167',
          700: '#D9D9D9',
          800: '#212332',
          900: '#171719',
          950: '#000000',
        },
        slate: {
          50: '#F5F6F6',
          100: '#EDF0F6',
          200: '#F1F2F3',
          300: '#4E5969',
          400: '#B8BBC2',
          500: '#CFD0D2',
          800: '#222222',
        },
        primary: {
          600: '#171719', // default
        },
        red: {
          50: '#FFF2F0',
          100: '#FDCDC5',
          300: '#F53F3F',
          400: '#D94F2C',
          500: '#FF5D34',
          600: '#FFF5F3',
          700: '#FFCCC7',
        },
        pink: {
          50: '#FFF0F6',
          100: '#FDC2DB',
          300: '#CB1E83',
        },
        blue: {
          50: '#E8F3FF',
          100: '#BEDAFF',
          200: '#1677FF',
          300: '#165DFF',
          400: '#1B58F4',
          500: '#155EEF', // default
          600: '#91CEFF',
          700: '#F1F6FE',
        },
        cyan: {
          50: '#E8FFFB',
          100: '#B7F4EC',
          300: '#0DA5AA',
        },
        green: {
          50: '#F2FFF3',
          100: '#DEF7EC',
          200: '#AFF0B5',
          300: '#F3FAF2',
          400: '#B7EB8F',
          600: '#369F21', // default
          800: '#03543F',
          900: '#009A29',
        },
        purple: {
          50: '#F9F2FF',
          100: '#DDBEF6',
          300: '#8D4EDA',
        },
        orange: {
          50: '#FFFAF0',
          100: '#FFE4BA',
          200: '#FFA940',
          300: '#FF7D00',
          400: '#FFF8F0',
          500: '#FFDBAF',
        }
      },
      boxShadow: {
        'xs': '0px 4px 6px 0px rgba(0, 0, 0, 0.06)',
        'sm': '0px 1px 2px 0px rgba(16, 24, 40, 0.06), 0px 1px 3px 0px rgba(16, 24, 40, 0.10)',
        'md': '0px 2px 4px -2px rgba(16, 24, 40, 0.06), 0px 4px 8px -2px rgba(16, 24, 40, 0.10)',
        'lg': '0px 4px 6px -2px rgba(16, 24, 40, 0.03), 0px 12px 16px -4px rgba(16, 24, 40, 0.08)',
        'xl': '0px 8px 8px -4px rgba(16, 24, 40, 0.03), 0px 20px 24px -4px rgba(16, 24, 40, 0.08)',
        '2xl': '0px 24px 48px -12px rgba(16, 24, 40, 0.18)',
        '3xl': '0px 32px 64px -12px rgba(16, 24, 40, 0.14)',
      },
      fontFamily: {
        'sans': ['PingFangSC', 'PingFang SC', 'ui-sans-serif', 'system-ui', 'sans-serif', "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji"],
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
  prefix: 'rb',
}