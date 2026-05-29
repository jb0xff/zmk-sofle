#!/usr/bin/env python3
"""Tiny browser-based editor for the LVGL 1-bit bitmaps in art.c.

Usage:
    python3 tools/art_editor.py [path/to/art.c] [--port 8765]

Opens a browser window with a pixel-editable view of every <name>_map[]
bitmap in the file. Left-click sets, right-click (or shift-click) clears,
drag to paint. Save writes the bytes back, preserving the #if/#else/#endif
palette block verbatim.
"""

import argparse
import json
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

W, H = 140, 68
STRIDE = (W + 7) // 8       # 18 bytes per row, LVGL pads to byte boundary
PIXEL_BYTES = STRIDE * H    # 1224

IMG_RE = re.compile(
    r"(?P<name>\w+)_map\[\]\s*=\s*\{"
    r"(?P<palette>.*?#endif)"
    r"(?P<sep>\s*)"
    r"(?P<data>.*?)"
    r"\};",
    re.DOTALL,
)
BYTE_RE = re.compile(r"0x([0-9a-fA-F]{2})")


def parse_data_bytes(data_text):
    out = bytearray(int(m.group(1), 16) for m in BYTE_RE.finditer(data_text))
    if len(out) != PIXEL_BYTES:
        raise ValueError(f"Expected {PIXEL_BYTES} pixel bytes, got {len(out)}")
    return out


def bytes_to_bits(data):
    bits = [0] * (W * H)
    for y in range(H):
        for x in range(W):
            byte = data[y * STRIDE + x // 8]
            bits[y * W + x] = (byte >> (7 - (x % 8))) & 1
    return bits


def bits_to_bytes(bits):
    out = bytearray(PIXEL_BYTES)
    for y in range(H):
        for x in range(W):
            if bits[y * W + x]:
                out[y * STRIDE + x // 8] |= 1 << (7 - (x % 8))
    return out


def format_data(data, indent="        ", first_indent="                ", per_line=15):
    lines = []
    for i in range(0, len(data), per_line):
        chunk = data[i : i + per_line]
        pad = first_indent if i == 0 else indent
        lines.append(pad + ", ".join(f"0x{b:02x}" for b in chunk) + ",")
    return "\n".join(lines)


def load_images(path):
    text = path.read_text()
    imgs = []
    for m in IMG_RE.finditer(text):
        data_bytes = parse_data_bytes(m.group("data"))
        imgs.append({
            "name": m.group("name"),
            "bits": bytes_to_bits(data_bytes),
            "palette": m.group("palette"),
            "sep": m.group("sep"),
            "match_start": m.start(),
            "match_end": m.end(),
        })
    return text, imgs


def save_images(path, original_text, imgs):
    text = original_text
    for img in sorted(imgs, key=lambda i: i["match_start"], reverse=True):
        bytes_ = bits_to_bytes(img["bits"])
        sep = img["sep"].rstrip(" \t")
        new_block = (
            f"{img['name']}_map[] = {{"
            f"{img['palette']}"
            f"{sep}"
            f"{format_data(bytes_)}\n"
            f"}};"
        )
        text = text[: img["match_start"]] + new_block + text[img["match_end"]:]
    path.write_text(text)
    return text


HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>art editor</title>
<style>
  body { font-family: ui-monospace, monospace; margin: 16px; background: #1a1a1a; color: #e0e0e0; }
  h2 { margin: 0 0 12px 0; }
  .imgs { display: flex; gap: 24px; flex-wrap: wrap; }
  .img { background: #2a2a2a; padding: 12px; border-radius: 6px; }
  canvas { display: block; image-rendering: pixelated; border: 1px solid #555; cursor: crosshair; }
  .name { margin-bottom: 8px; font-weight: bold; }
  .toolbar { margin: 12px 0; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  button { background: #3a3a3a; color: #eee; border: 1px solid #555; padding: 6px 14px; cursor: pointer; border-radius: 4px; font: inherit; }
  button:hover { background: #4a4a4a; }
  button.primary { background: #3a5a8a; border-color: #4a6a9a; }
  button.primary:hover { background: #4a6a9a; }
  .status { color: #999; margin-left: 8px; }
  .hint { color: #777; font-size: 0.9em; margin-top: 12px; }
</style>
</head><body>
<h2>art editor</h2>
<div class="toolbar">
  <button class="primary" onclick="save()">Save</button>
  <button onclick="load()">Reload from file</button>
  <button id="toolBtn" onclick="toggleTool()">Tool: Brush</button>
  <button onclick="rotateView()">Rotate view</button>
  <button onclick="invertAll()">Invert all</button>
  <button onclick="clearAll()">Clear all</button>
  <label style="margin-left:8px">Brush:
    <input type="number" id="brush" value="1" min="1" max="30" style="width:50px;background:#2a2a2a;color:#eee;border:1px solid #555;padding:4px;border-radius:3px"
           oninput="brushSize = Math.max(1, parseInt(this.value) || 1)">
  </label>
  <label style="margin-left:8px">Threshold:
    <input type="number" id="thresh" value="128" min="0" max="255" style="width:60px;background:#2a2a2a;color:#eee;border:1px solid #555;padding:4px;border-radius:3px"
           oninput="threshold = Math.min(255, Math.max(0, parseInt(this.value) || 0))">
  </label>
  <label style="margin-left:8px"><input type="checkbox" id="invertSrc" oninput="invertSource = this.checked"> Invert source</label>
  <span class="status" id="status"></span>
</div>
<div class="imgs" id="imgs"></div>
<div class="hint">
  <b>Brush:</b> left-click set, right-click / shift-click clear, drag to paint.
  <b>Select:</b> drag to marquee, then drag inside the box to move pixels. Esc commits and deselects.
  Bitmap on disk stays 140×68; the editor just rotates the view.
</div>
<script>
const W = 140, H = 68, SCALE = 5;
const RULER_TOP = 18, RULER_LEFT = 26, GRID_MAJOR = 10;
// rotation: 0 = landscape (raw), 1 = portrait CW, 2 = landscape flipped, 3 = portrait CCW
let rotation = 3;
let brushSize = 1;  // 1 = single pixel; N = round disc of radius N-1
let threshold = 128;  // luminance cutoff for rasterizing an uploaded image
let invertSource = false;  // flip on/off when reading the uploaded image
let images = [];

// Tool state
let tool = 'brush';        // 'brush' | 'select'
let gesture = null;        // null | 'brush' | 'marquee' | 'move'
let gestureImg = -1;       // which image index owns the active gesture
let gestureStart = null;   // marquee anchor in canvas coords
let gestureVal = 1;        // brush value (0 or 1) for the current stroke
let gestureLast = null;    // last canvas point for Bresenham brush interpolation
let gestureOffset = null;  // for 'move' gesture: (mouseCx - float.cx, mouseCy - float.cy)

async function load() {
  const r = await fetch('/api/load');
  if (!r.ok) { setStatus('Load failed'); return; }
  images = await r.json();
  render();
  setStatus(`Loaded ${images.length} images`);
}

// Map canvas pixel (cx, cy) to source bitmap (sx, sy) given the active rotation.
// Canvas dims are (cw, ch) which depend on rotation.
function canvasToSource(cx, cy) {
  switch (rotation) {
    case 0: return [cx, cy];                  // landscape, identity
    case 1: return [cy, H - 1 - cx];          // 90° CW: portrait, canvas (cx,cy) -> src
    case 2: return [W - 1 - cx, H - 1 - cy];  // 180°
    case 3: return [W - 1 - cy, cx];          // 90° CCW
  }
}

function canvasDims() {
  return (rotation % 2 === 0) ? [W, H] : [H, W];
}

function render() {
  const root = document.getElementById('imgs');
  root.innerHTML = '';
  const [cw, ch] = canvasDims();
  images.forEach((img, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'img';
    wrap.innerHTML = `<div class="name">${img.name} (${W}x${H} stored, view ${cw}x${ch})</div>`;
    const c = document.createElement('canvas');
    c.width = RULER_LEFT + cw * SCALE;
    c.height = RULER_TOP + ch * SCALE;
    wrap.appendChild(c);
    const tools = document.createElement('div');
    tools.style.marginTop = '8px';
    const loadLabel = document.createElement('label');
    loadLabel.style.cssText = 'background:#3a3a3a;border:1px solid #555;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:0.9em';
    loadLabel.textContent = 'Load image…';
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'image/*';
    fileInput.style.display = 'none';
    fileInput.addEventListener('change', e => {
      const f = e.target.files[0];
      if (f) rasterizeIntoImage(i, f);
      e.target.value = '';
    });
    loadLabel.appendChild(fileInput);
    tools.appendChild(loadLabel);
    const invertBtn = document.createElement('button');
    invertBtn.textContent = 'Invert';
    invertBtn.style.marginLeft = '6px';
    invertBtn.addEventListener('click', () => invertImage(i));
    tools.appendChild(invertBtn);
    wrap.appendChild(tools);
    root.appendChild(wrap);
    img._canvas = c;
    if (img._sel === undefined) img._sel = null;
    if (img._float === undefined) img._float = null;
    drawAll(i);
    c.addEventListener('contextmenu', e => e.preventDefault());
    c.addEventListener('mousedown', e => onCanvasDown(i, e));
    c.addEventListener('mousemove', e => onCanvasMove(i, e));
  });
}

function onCanvasDown(i, e) {
  const img = images[i];
  if (tool === 'brush') {
    const p = mouseToCanvas(img, e);
    if (!p) return;
    // Brush commits any float on this image first
    if (img._float) commitFloat(i);
    img._sel = null;
    gesture = 'brush';
    gestureImg = i;
    gestureVal = (e.button === 2 || e.shiftKey) ? 0 : 1;
    stamp(img, p[0], p[1], gestureVal);
    gestureLast = p;
    drawAll(i);
    return;
  }
  // Select tool
  const p = mouseToCanvasClamped(img, e);
  if (img._float && pointInRect(p[0], p[1], img._float)) {
    gesture = 'move';
    gestureImg = i;
    gestureOffset = [p[0] - img._float.cx, p[1] - img._float.cy];
    return;
  }
  if (img._sel && pointInRect(p[0], p[1], img._sel)) {
    liftSelection(i);
    gesture = 'move';
    gestureImg = i;
    gestureOffset = [p[0] - img._float.cx, p[1] - img._float.cy];
    drawAll(i);
    return;
  }
  // Starting a fresh marquee — commit any existing float first
  if (img._float) commitFloat(i);
  gesture = 'marquee';
  gestureImg = i;
  gestureStart = p;
  img._sel = { cx: p[0], cy: p[1], cw: 1, ch: 1 };
  drawAll(i);
}

function onCanvasMove(i, e) {
  if (gesture === null || gestureImg !== i) return;
  const img = images[i];
  if (gesture === 'brush') {
    const p = mouseToCanvas(img, e);
    if (!p) return;
    if (gestureLast) lineStamp(img, gestureLast[0], gestureLast[1], p[0], p[1], gestureVal);
    else stamp(img, p[0], p[1], gestureVal);
    gestureLast = p;
    drawAll(i);
  } else if (gesture === 'marquee') {
    const p = mouseToCanvasClamped(img, e);
    const cx = Math.min(gestureStart[0], p[0]);
    const cy = Math.min(gestureStart[1], p[1]);
    const cw = Math.abs(p[0] - gestureStart[0]) + 1;
    const ch = Math.abs(p[1] - gestureStart[1]) + 1;
    img._sel = { cx, cy, cw, ch };
    drawAll(i);
  } else if (gesture === 'move') {
    const p = mouseToCanvasClamped(img, e);
    img._float.cx = p[0] - gestureOffset[0];
    img._float.cy = p[1] - gestureOffset[1];
    drawAll(i);
  }
}

window.addEventListener('mouseup', () => {
  if (gesture === 'marquee') {
    const img = images[gestureImg];
    // Discard a zero-area marquee (just a click)
    if (img && img._sel && (img._sel.cw < 1 || img._sel.ch < 1)) img._sel = null;
  }
  gesture = null;
  gestureLast = null;
});

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    let any = false;
    images.forEach((img, i) => {
      if (img._float) { commitFloat(i); any = true; }
      if (img._sel) { img._sel = null; any = true; }
      if (any) drawAll(i);
    });
    if (any) setStatus('Selection cleared');
  }
});

function pointInRect(px, py, r) {
  return px >= r.cx && px < r.cx + r.cw && py >= r.cy && py < r.cy + r.ch;
}

function liftSelection(i) {
  const img = images[i];
  if (!img._sel) return;
  const s = img._sel;
  const [cw, ch] = canvasDims();
  const f = { cx: s.cx, cy: s.cy, cw: s.cw, ch: s.ch, bits: new Array(s.cw * s.ch).fill(0) };
  for (let dy = 0; dy < s.ch; dy++) {
    for (let dx = 0; dx < s.cw; dx++) {
      const px = s.cx + dx, py = s.cy + dy;
      if (px < 0 || px >= cw || py < 0 || py >= ch) continue;
      const [sx, sy] = canvasToSource(px, py);
      f.bits[dy * s.cw + dx] = img.bits[sy * W + sx];
      img.bits[sy * W + sx] = 0;
    }
  }
  img._float = f;
  img._sel = null;
}

function commitFloat(i) {
  const img = images[i];
  if (!img._float) return;
  const f = img._float;
  const [cw, ch] = canvasDims();
  for (let dy = 0; dy < f.ch; dy++) {
    for (let dx = 0; dx < f.cw; dx++) {
      const px = f.cx + dx, py = f.cy + dy;
      if (px < 0 || px >= cw || py < 0 || py >= ch) continue;
      const [sx, sy] = canvasToSource(px, py);
      img.bits[sy * W + sx] = f.bits[dy * f.cw + dx];
    }
  }
  img._float = null;
}

function toggleTool() {
  // If leaving select, commit any floats
  if (tool === 'select') {
    images.forEach((img, i) => { if (img._float) commitFloat(i); img._sel = null; });
  }
  tool = tool === 'brush' ? 'select' : 'brush';
  document.getElementById('toolBtn').textContent = `Tool: ${tool === 'brush' ? 'Brush' : 'Select'}`;
  images.forEach((_, i) => drawAll(i));
  setStatus(`Tool: ${tool}`);
}

function drawAll(i) {
  const img = images[i];
  const ctx = img._canvas.getContext('2d');
  const [cw, ch] = canvasDims();
  const pxW = cw * SCALE, pxH = ch * SCALE;
  // Ruler gutter background
  ctx.fillStyle = '#2a2a2a';
  ctx.fillRect(0, 0, RULER_LEFT + pxW, RULER_TOP + pxH);
  // Pixel area background
  ctx.fillStyle = '#fff';
  ctx.fillRect(RULER_LEFT, RULER_TOP, pxW, pxH);
  // Pixels
  ctx.fillStyle = '#000';
  for (let cy = 0; cy < ch; cy++) {
    for (let cx = 0; cx < cw; cx++) {
      const [sx, sy] = canvasToSource(cx, cy);
      if (img.bits[sy * W + sx]) {
        ctx.fillRect(RULER_LEFT + cx * SCALE, RULER_TOP + cy * SCALE, SCALE, SCALE);
      }
    }
  }
  // Minor gridlines (every pixel)
  ctx.strokeStyle = 'rgba(120,120,120,0.18)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 0; x <= cw; x++) {
    const px = RULER_LEFT + x * SCALE + 0.5;
    ctx.moveTo(px, RULER_TOP); ctx.lineTo(px, RULER_TOP + pxH);
  }
  for (let y = 0; y <= ch; y++) {
    const py = RULER_TOP + y * SCALE + 0.5;
    ctx.moveTo(RULER_LEFT, py); ctx.lineTo(RULER_LEFT + pxW, py);
  }
  ctx.stroke();
  // Major gridlines (every GRID_MAJOR pixels)
  ctx.strokeStyle = 'rgba(80,160,240,0.45)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 0; x <= cw; x += GRID_MAJOR) {
    const px = RULER_LEFT + x * SCALE + 0.5;
    ctx.moveTo(px, RULER_TOP); ctx.lineTo(px, RULER_TOP + pxH);
  }
  for (let y = 0; y <= ch; y += GRID_MAJOR) {
    const py = RULER_TOP + y * SCALE + 0.5;
    ctx.moveTo(RULER_LEFT, py); ctx.lineTo(RULER_LEFT + pxW, py);
  }
  ctx.stroke();
  // Rulers: tick labels every GRID_MAJOR pixels
  ctx.fillStyle = '#bbb';
  ctx.font = '10px ui-monospace, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  for (let x = 0; x <= cw; x += GRID_MAJOR) {
    ctx.fillText(String(x), RULER_LEFT + x * SCALE, RULER_TOP - 3);
  }
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let y = 0; y <= ch; y += GRID_MAJOR) {
    ctx.fillText(String(y), RULER_LEFT - 4, RULER_TOP + y * SCALE);
  }
  // Floating buffer (rendered as full rectangle so 0-bits overwrite underlying pixels visually too).
  if (img._float) {
    const f = img._float;
    for (let dy = 0; dy < f.ch; dy++) {
      for (let dx = 0; dx < f.cw; dx++) {
        const px = f.cx + dx, py = f.cy + dy;
        if (px < 0 || px >= cw || py < 0 || py >= ch) continue;
        ctx.fillStyle = f.bits[dy * f.cw + dx] ? '#000' : '#fff';
        ctx.fillRect(RULER_LEFT + px * SCALE, RULER_TOP + py * SCALE, SCALE, SCALE);
      }
    }
  }
  // Selection / float marquee (dashed)
  const drawMarquee = (r, color) => {
    ctx.save();
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(
      RULER_LEFT + r.cx * SCALE - 0.5,
      RULER_TOP + r.cy * SCALE - 0.5,
      r.cw * SCALE + 1,
      r.ch * SCALE + 1,
    );
    ctx.restore();
  };
  if (img._float) drawMarquee(img._float, '#f59f00');     // orange = floating
  else if (img._sel) drawMarquee(img._sel, '#3b8eea');    // blue = static selection
}

function mouseToCanvas(img, e) {
  const rect = img._canvas.getBoundingClientRect();
  const cx = Math.floor((e.clientX - rect.left - RULER_LEFT) / SCALE);
  const cy = Math.floor((e.clientY - rect.top - RULER_TOP) / SCALE);
  const [cw, ch] = canvasDims();
  if (cx < 0 || cx >= cw || cy < 0 || cy >= ch) return null;
  return [cx, cy];
}

function mouseToCanvasClamped(img, e) {
  const rect = img._canvas.getBoundingClientRect();
  let cx = Math.floor((e.clientX - rect.left - RULER_LEFT) / SCALE);
  let cy = Math.floor((e.clientY - rect.top - RULER_TOP) / SCALE);
  const [cw, ch] = canvasDims();
  cx = Math.max(0, Math.min(cw - 1, cx));
  cy = Math.max(0, Math.min(ch - 1, cy));
  return [cx, cy];
}

function stamp(img, cx, cy, val) {
  const r = brushSize - 1;
  const [cw, ch] = canvasDims();
  const r2 = r * r;
  for (let dy = -r; dy <= r; dy++) {
    for (let dx = -r; dx <= r; dx++) {
      if (dx*dx + dy*dy > r2) continue;
      const px = cx + dx, py = cy + dy;
      if (px < 0 || px >= cw || py < 0 || py >= ch) continue;
      const [sx, sy] = canvasToSource(px, py);
      img.bits[sy * W + sx] = val;
    }
  }
}

function lineStamp(img, x0, y0, x1, y1, val) {
  // Bresenham: stamp the brush along the line from (x0,y0) to (x1,y1).
  const dx = Math.abs(x1 - x0), dy = Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx - dy, x = x0, y = y0;
  while (true) {
    stamp(img, x, y, val);
    if (x === x1 && y === y1) break;
    const e2 = 2 * err;
    if (e2 > -dy) { err -= dy; x += sx; }
    if (e2 < dx)  { err += dx; y += sy; }
  }
}

function invertImage(i) {
  const img = images[i];
  for (let k = 0; k < img.bits.length; k++) img.bits[k] ^= 1;
  drawAll(i);
  setStatus(`Inverted ${img.name} (not saved)`);
}

function rasterizeIntoImage(i, file) {
  const url = URL.createObjectURL(file);
  const im = new Image();
  im.onload = () => {
    URL.revokeObjectURL(url);
    const [cw, ch] = canvasDims();
    // Offscreen canvas at the view size; "object-fit: contain" letterbox onto white.
    const off = document.createElement('canvas');
    off.width = cw; off.height = ch;
    const octx = off.getContext('2d');
    octx.fillStyle = '#fff';
    octx.fillRect(0, 0, cw, ch);
    const s = Math.min(cw / im.width, ch / im.height);
    const dw = im.width * s, dh = im.height * s;
    const dx = (cw - dw) / 2, dy = (ch - dh) / 2;
    octx.drawImage(im, dx, dy, dw, dh);
    const data = octx.getImageData(0, 0, cw, ch).data;
    const img = images[i];
    img.bits = new Array(W * H).fill(0);
    for (let py = 0; py < ch; py++) {
      for (let px = 0; px < cw; px++) {
        const k = (py * cw + px) * 4;
        const r = data[k], g = data[k+1], b = data[k+2], a = data[k+3];
        // Composite alpha onto white background, then compute luminance.
        const af = a / 255;
        const rr = r * af + 255 * (1 - af);
        const gg = g * af + 255 * (1 - af);
        const bb = b * af + 255 * (1 - af);
        const lum = 0.2126 * rr + 0.7152 * gg + 0.0722 * bb;
        // Bright pixels => on by default (matches "bright neon on black bg").
        // Toggle "Invert source" for dark-text-on-light scans.
        let on = lum > threshold ? 1 : 0;
        if (invertSource) on ^= 1;
        if (on) {
          const [sx, sy] = canvasToSource(px, py);
          img.bits[sy * W + sx] = 1;
        }
      }
    }
    drawAll(i);
    setStatus(`Rasterized ${file.name} into ${img.name} (not saved)`);
  };
  im.onerror = () => {
    URL.revokeObjectURL(url);
    setStatus(`Failed to load ${file.name}`);
  };
  im.src = url;
}

function rotateView() {
  rotation = (rotation + 1) % 4;
  render();
  const names = ['landscape', 'portrait (CW)', 'landscape flipped', 'portrait (CCW)'];
  setStatus(`View: ${names[rotation]}`);
}

function invertAll() {
  images.forEach((img, i) => {
    for (let k = 0; k < img.bits.length; k++) img.bits[k] ^= 1;
    drawAll(i);
  });
  setStatus('Inverted (not saved)');
}

function clearAll() {
  if (!confirm('Clear all pixels in every image? (not saved until you press Save)')) return;
  images.forEach((img, i) => {
    img.bits = new Array(W * H).fill(0);
    drawAll(i);
  });
  setStatus('Cleared (not saved)');
}

async function save() {
  setStatus('Saving…');
  // Commit any floating selections so they get written to disk.
  images.forEach((img, i) => { if (img._float) commitFloat(i); img._sel = null; drawAll(i); });
  const payload = images.map(img => ({ name: img.name, bits: img.bits }));
  const r = await fetch('/api/save', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
  if (r.ok) setStatus(`Saved at ${new Date().toLocaleTimeString()}`);
  else setStatus('Save failed: ' + (await r.text()));
}

function setStatus(msg) { document.getElementById('status').textContent = msg; }

load();
</script>
</body></html>
"""


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def _json(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                body = HTML.encode()
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/load":
                try:
                    text, imgs = load_images(state["path"])
                except Exception as e:
                    self._json(500, {"error": str(e)})
                    return
                state["text"] = text
                state["imgs"] = imgs
                self._json(200, [{"name": img["name"], "bits": img["bits"]} for img in imgs])
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/save":
                self.send_error(404)
                return
            length = int(self.headers.get("content-length", 0))
            try:
                payload = json.loads(self.rfile.read(length))
            except Exception as e:
                self._json(400, {"error": f"bad json: {e}"})
                return
            by_name = {img["name"]: img for img in state["imgs"]}
            for item in payload:
                name = item.get("name")
                if name not in by_name:
                    self._json(400, {"error": f"unknown image {name}"})
                    return
                bits = item.get("bits", [])
                if len(bits) != W * H:
                    self._json(400, {"error": f"{name}: expected {W*H} bits, got {len(bits)}"})
                    return
                by_name[name]["bits"] = list(bits)
            try:
                new_text = save_images(state["path"], state["text"], state["imgs"])
            except Exception as e:
                self._json(500, {"error": str(e)})
                return
            state["text"] = new_text
            self._json(200, {"ok": True})

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="boards/shields/nice_view_custom/widgets/art.c")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    path = Path(args.path).resolve()
    if not path.exists():
        sys.exit(f"file not found: {path}")
    state = {"path": path, "text": "", "imgs": []}
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"art editor serving {path}\n  -> {url}")
    if not args.no_open:
        threading.Thread(target=lambda: (time.sleep(0.3), webbrowser.open(url)), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
