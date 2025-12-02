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
          50: '#F7F7F7',
          100: '#F5F7FC',
          200: '#EBEBEB',
          300: '#d0d5dd',
          350: '#939AB1',
          400: '#A8A9AA',
          500: '#667085',
          700: '#475467',
          600: '#5B6167',
          800: '#212332',
          900: '#101828',
        },
        primary: {
          600: '#155eef',
        },
        red:{
          500: '#FF5D34'
        },
        blue: {
          500: '#E1EFFE',
        },
        green: {
          50: '#F3FAF7',
          100: '#DEF7EC',
          600: '#369F21',
          800: '#03543F',
        },
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