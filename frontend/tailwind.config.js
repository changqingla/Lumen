/** @type {import('tailwindcss').Config} */
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const configDir = dirname(fileURLToPath(import.meta.url))

export default {
  darkMode: 'class',
  content: [
    resolve(configDir, './index.html'),
    resolve(configDir, './src/**/*.{js,ts,jsx,tsx}'),
  ],
  theme: {
    container: {
      center: true,
    },
    extend: {
      colors: {
        background: '#0b1326',
        primary: '#c0c1ff',
        secondary: '#4cd7f6',
        tertiary: '#ddb7ff',
        surface: '#0b1326',
        'surface-container': '#171f33',
        'surface-container-low': '#131b2e',
        'surface-container-lowest': '#0a1020',
        'surface-container-high': '#222a3d',
        'surface-container-highest': '#2d3449',
        'on-surface': '#dae2fd',
        'on-surface-variant': '#c7c4d8',
        'outline-variant': '#464555',
        'primary-container': '#4b4dd8',
        'secondary-container': '#03b5d3',
        'tertiary-container': '#862dd4',
      },
      fontFamily: {
        headline: ['"Space Grotesk"', 'sans-serif'],
        body: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
