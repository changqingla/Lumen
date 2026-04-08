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
    extend: {},
  },
  plugins: [],
}
