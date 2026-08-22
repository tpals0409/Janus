import React from 'react'
import ReactDOM from 'react-dom/client'
import './main.css'
import App from './App'
import { seedTaskRuntimeVisualFixture } from './visualFixture'

const visualFixture =
  import.meta.env.DEV && new URLSearchParams(window.location.search).get('fixture') === 'task-runtime'
if (visualFixture) seedTaskRuntimeVisualFixture()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
