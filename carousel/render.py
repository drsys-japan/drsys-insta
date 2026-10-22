"""カルーセル画像を JSON から作るスクリプト。

使い方:
    python carousel/render.py carousel/samples/exosome.json out/

JSON の形:
{
  "brand": "あいの大人の美容ノート",
  "slides": [
    {"type": "cover", "kicker": "...", "title": "...", "sub": "..."},
    {"type": "body", "num": "01", "title": "...", "text": "...", "note": "..."},
    {"type": "cta", "lead": "...", "title": "...", "keyword": "...", "small": "..."}
  ]
}
title / text の中では「改行」は \n、強調は **太字**、表紙の見出しは *マーカー* が使えます。
"""

import html
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
CSS = (HERE / "style.css").read_text(encoding="utf-8")


def fmt(text: str) -> str:
    """HTML エスケープしたうえで、改行・太字・マーカーだけを許可する。"""
    s = html.escape(text or "")
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
    return s.replace("\n", "<br>")


def dots(i: int, n: int) -> str:
    return "".join(f'<span class="{"on" if k == i else ""}"></span>' for k in range(n))


def slide_html(slide: dict, i: int, n: int, brand: str) -> str:
    kind = slide["type"]
    footer = f'<div class="footer"><span>{fmt(slide.get("footer", "保存して見返してね"))}</span><div class="dots">{dots(i, n)}</div></div>'
    head = f'<div class="brand">{fmt(brand)}</div>'

    if kind == "cover":
        inner = f"""
        <div class="panel">
          <div class="kicker">{fmt(slide.get("kicker", ""))}</div>
          <h1>{fmt(slide["title"])}</h1>
          <div class="sub">{fmt(slide.get("sub", ""))}</div>
        </div>"""
        cls = "cover"
    elif kind == "body":
        note = f'<div class="note">{fmt(slide["note"])}</div>' if slide.get("note") else ""
        inner = f"""
        <div class="content">
          <div class="num">{fmt(slide.get("num", f"{i:02d}"))}</div>
          <h2>{fmt(slide["title"])}</h2>
          <p>{fmt(slide["text"])}</p>
          {note}
        </div>"""
        cls = "body-slide"
    elif kind == "cta":
        inner = f"""
        <div class="panel">
          <div class="lead">{fmt(slide.get("lead", ""))}</div>
          <h2>{fmt(slide["title"])}</h2>
          <div class="keyword">{fmt(slide.get("keyword", ""))}</div>
          <div class="small">{fmt(slide.get("small", ""))}</div>
        </div>"""
        cls = "cta"
    else:
        raise ValueError(f"unknown slide type: {kind}")

    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<style>{CSS}</style></head>
<body class="{cls}">
  <div class="ring r1"></div><div class="ring r2"></div>
  <div class="frame">{head}{inner}{footer}</div>
</body></html>"""


def render(spec_path: str, out_dir: str) -> list[Path]:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    slides = spec["slides"]
    brand = spec.get("brand", "")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1350})
        for i, slide in enumerate(slides):
            page.set_content(slide_html(slide, i, len(slides), brand), wait_until="load")
            page.evaluate("document.fonts.ready")
            path = out / f"slide_{i + 1:02d}.png"
            page.screenshot(path=str(path), full_page=False)
            paths.append(path)
        browser.close()
    return paths


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    for path in render(sys.argv[1], sys.argv[2]):
        print(path)
