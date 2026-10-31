import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode}) => ({ base: mode === 'pages' ? '/marketlens/' : '/', plugins: [react()], server: { proxy: { '/api': 'http://127.0.0.1:8000' } }, build: {outDir: mode === 'pages' ? 'dist-pages' : 'dist', rollupOptions: {output: {manualChunks(id) { if (id.includes('lightweight-charts')) return 'candlesticks'; if (/node_modules\/(recharts|d3-|victory)/.test(id.replaceAll('\\','/'))) return 'equity'; if(id.includes('node_modules')) return 'vendor'; }}}} }));
