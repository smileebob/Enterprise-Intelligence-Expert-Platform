import { defineConfig, presetUno, presetAttributify, presetIcons } from 'unocss'

export default defineConfig({
  presets: [presetUno(), presetAttributify(), presetIcons()],
  theme: {
    colors: {
      brand: {
        50: '#eef6ff',
        100: '#d9eaff',
        500: '#1677ff',
        600: '#0f5fd6',
        700: '#0b4aa8',
      },
    },
  },
  shortcuts: {
    'panel': 'bg-white rounded-lg shadow-sm border border-gray-100',
    'panel-title': 'text-sm font-600 text-gray-700 px-4 py-3 border-b border-gray-100',
    'chip': 'inline-flex items-center px-2 py-0.5 rounded-full text-xs',
  },
})
