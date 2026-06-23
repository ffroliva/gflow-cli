# Visual Client Design: Gflow Studio (Tauri / React)

This document defines the frontend-backend architecture, UI/UX aesthetics, and code hardening guidelines for the decoupled **Gflow Studio** filmmaking client.

---

## 1. Visual Aesthetics & Theme System

To create a visual editor that feels premium and immersive, Gflow Studio uses a modern dark-mode design system built on CSS variables, glassmorphic layout surfaces, and smooth transitions.

```
┌─────────────────────────────────────────────────────────────┐
│                       Gflow Studio                          │
│  ┌─────────────────────────┬─────────────────────────────┐  │
│  │     Asset Navigator     │      Viewport Preview       │  │
│  │   (Glassmorphic list)   │  (Video canvas, controls)   │  │
│  ├─────────────────────────┴─────────────────────────────┤  │
│  │                     Timeline Editor                     │  │
│  │    (Tracks: Video / Audio / Captions. Drag playhead)    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 1.1 CSS Variable Design Tokens
```css
:root {
  /* HSL Color Palette */
  --bg-main: hsl(230, 15%, 8%);
  --bg-surface: hsla(230, 15%, 15%, 0.7);
  --border-glow: hsla(230, 25%, 30%, 0.2);
  
  --accent-primary: hsl(265, 85%, 60%);
  --accent-secondary: hsl(190, 90%, 50%);
  --accent-gradient: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
  
  --text-main: hsl(0, 0%, 94%);
  --text-muted: hsl(230, 10%, 65%);
  
  /* Glassmorphism & Effects */
  --backdrop-blur: blur(16px);
  --transition-smooth: cubic-bezier(0.16, 1, 0.3, 1);
  --shadow-premium: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
}
```

### 1.2 Micro-Animations & Interaction
* **Buttons & Hover:** Interactive elements scale slightly (`scale(0.98)` on click, `scale(1.02)` on hover) and utilize smooth transitions:
  ```css
  button {
    transition: transform 0.2s var(--transition-smooth), background 0.3s ease;
  }
  ```
* **Asset Drags:** Visual preview boxes use spring physics animations (via `framer-motion` or standard CSS springs) during timeline dragging to feel organic.

---

## 2. Hardened React Architecture (Layers & Levels)

To ensure the client never crashes during long editing sessions, components are structured with clean error isolation boundaries and performant state managers.

```
┌────────────────────────────────────────────────────────────────┐
│                        Root App Container                      │
│                               │                                │
│                   Global Error Boundary                        │
│                               │                                │
│                   AppStateProvider (Context)                   │
│         (Loads SQLite schema & initiates MCP connections)      │
│                               │                                │
│     ┌─────────────────────────┼──────────────────────────┐     │
│     ▼                         ▼                          ▼     │
│ Viewport Boundary      Timeline Boundary        Gallery Boundary│
│ [Canvas / Player]      [Tracks / Keyframes]     [Asset Catalog] │
└────────────────────────────────────────────────────────────────┘
```

### 2.1 Error Isolation (React Error Boundaries)
Wrap critical visual sections in isolated React `ErrorBoundary` classes. If a corrupted image frameset crashes the video canvas player, the timeline and asset gallery remain functional and active (allowing users to delete or swap the bad asset).

```typescript
// Component Error Boundary Wrapper
import React, { Component, ErrorInfo, ReactNode } from "react";

interface Props { children: ReactNode; fallbackName: string; }
interface State { hasError: boolean; }

export class ComponentBoundary extends Component<Props, State> {
  public state: State = { hasError: false };

  public static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error(`Error in ${this.props.fallbackName}:`, error, errorInfo);
  }

  public render() {
    if (this.state.hasError) {
      return (
        <div className="glass-panel p-4 border-red-500/50 text-center">
          <p className="text-red-400 font-medium">Failed to load {this.props.fallbackName}</p>
          <button onClick={() => this.setState({ hasError: false })} className="btn-secondary mt-2">
            Reset Module
          </button>
        </div>
      );
    }
    return this.children;
  }
}
```

### 2.2 Strict TypeScript Interfaces
Define robust data contracts matching the CLI database schemas:

```typescript
export type AssetKind = "image" | "video";

export interface AssetRecord {
  asset_id: string;
  flow_project_id: string;
  kind: AssetKind;
  model: string;
  aspect_ratio: string;
  seed: number;
  local_path?: string;
  cloud_uri?: string;
  created_at: string;
}

export interface TimelineClip {
  clip_id: string;
  asset_id: string;
  track_id: string;
  start_ms: number;
  end_ms: number;
  play_rate: number;
}
```

### 2.3 Performance: Gallery Virtualization
A film project can accumulate thousands of generated images and videos. Rendering thousands of DOM nodes in the sidebar list degrades canvas framerates.
* **Mitigation:** Use a virtualized list container (like `react-virtualized` or a custom CSS-intersection hook) to render only the visible thumbnails in the viewport, maintaining a steady 60 FPS during playhead scrubs.

---

## 3. Tauri Rust-to-React Pipelines

Tauri binds the Rust backend to the React frontend using JSON-RPC commands and event listeners.

```
┌───────────────────────────────────────┐
│              React UI                 │
└───────┬───────────────────────▲───────┘
        │ invoke('read_db')     │ listen('db_updated')
        ▼                       │
┌───────────────────────────────┴───────┐
│            Tauri Rust                 │
└───────┬───────────────────────▲───────┘
        │ execute               │ watch / callbacks
        ▼                       │
┌───────────────────────────────┴───────┐
│     SQLite DB (gflow.db WAL mode)     │
└───────────────────────────────────────┘
```

### 3.1 Direct Database Reading (Rust-side)
The UI invokes the Tauri Rust command to fetch catalog data directly from `gflow.db` using WAL mode, avoiding process pipe latency:

```rust
#[tauri::command]
async fn fetch_assets(db_path: String) -> Result<Vec<AssetDto>, String> {
    let conn = rusqlite::Connection::open_with_flags(
        &db_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE | rusqlite::OpenFlags::SQLITE_OPEN_URI
    ).map_err(|e| e.to_string())?;
    
    conn.execute("PRAGMA journal_mode = WAL", []).ok();
    conn.execute("PRAGMA busy_timeout = 5000", []).ok();
    
    let mut stmt = conn.prepare("SELECT asset_id, kind, model, local_path FROM assets")
        .map_err(|e| e.to_string())?;
    
    let rows = stmt.query_map([], |row| {
        Ok(AssetDto {
            asset_id: row.get(0)?,
            kind: row.get(1)?,
            model: row.get(2)?,
            local_path: row.get(3)?,
        })
    }).map_err(|e| e.to_string())?;
    
    // Collect and return
}
```

### 3.2 FFmpeg Integration via Tauri Shell Sidecar
Tauri spawns packaged static FFmpeg sidecars natively, parsing stdout progress percentages to update the React timeline rendering bar:

```rust
use tauri::api::process::{Command, CommandEvent};

#[tauri::command]
async fn splice_video(
    ffmpeg_path: String, 
    input_txt: String, 
    output_path: String
) -> Result<String, String> {
    let (mut rx, mut child) = Command::new_sidecar("ffmpeg")
        .map_err(|e| e.to_string())?
        .args(&["-f", "concat", "-safe", "0", "-i", &input_txt, "-c", "copy", &output_path])
        .spawn()
        .map_err(|e| e.to_string())?;
        
    while let Some(event) = rx.recv().await {
        if let CommandEvent::Stdout(line) = event {
            // Parse progress (e.g. frame= 150 fps=0.0) and emit to React frontend
        }
    }
    
    Ok("Splicing complete".into())
}
```

---

## 4. MCP Daemon Connection

React connects to the local daemon `/mcp/sse` endpoint:
* Establish connection: `const sse = new EventSource("http://127.0.0.1:8000/mcp/sse");`
* Recieve message URI, then dispatch tool calls via standard `fetch("http://127.0.0.1:8000/mcp/message", { method: 'POST', body: JSON.stringify(rpcCall) })`.
* Handle live standard progress logs via the SSE stream, displaying real-time Playwright terminal logs directly in a dashboard status window.
