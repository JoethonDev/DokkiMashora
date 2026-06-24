/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./templates/**/*.html', './apps/**/templates/**/*.html'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter"', '"Cairo"', 'system-ui', 'sans-serif'],
        display: ['"Inter"', '"Cairo"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        brand: {
          50:  '#f0fdf6',
          100: '#dcfce8',
          200: '#bbf7d0',
          300: '#86efad',
          400: '#4ade80',
          500: '#10b981',
          600: '#059669',
          700: '#047857',
          800: '#065f46',
          900: '#064e3b',
          950: '#022c22',
        },
      },
    },
  },
  plugins: [],
}
