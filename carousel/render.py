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
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent

# 手描き風の線（少しゆがませて、きれいすぎない形にしている）
SPARKLE = """<svg class="doodle" style="{pos}" width="{s}" height="{s}" viewBox="0 0 60 60"><path d="M30 4 C31.5 19 39 27.5 56 30.5 C40 32 32.5 40 29.5 56 C28 41 20.5 33 4 30 C19.5 28.5 28.5 20 30 4 Z" fill="none" stroke="{c}" stroke-width="3.2" stroke-linejoin="round"/></svg>"""
HEART = """<svg class="doodle" style="{pos}" width="{s}" height="{s}" viewBox="0 0 60 60"><path d="M30 52 C11 39 4.5 27 9.5 16.5 C15 6 27.5 7.5 30.5 19.5 C33 8 46.5 5.5 51.5 17 C56.5 28.5 47 40.5 30 52 Z" fill="none" stroke="{c}" stroke-width="3.4" stroke-linejoin="round"/></svg>"""
ARROW = """<svg class="doodle" style="{pos}" width="150" height="110" viewBox="0 0 150 110"><path d="M8 12 C 30 70, 80 92, 128 78" fill="none" stroke="{c}" stroke-width="4" stroke-linecap="round"/><path d="M110 64 L130 77 L112 94" fill="none" stroke="{c}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
SQUIGGLE = """<svg class="squiggle" viewBox="0 0 520 34"><path d="M4 20 C 40 4, 70 32, 108 17 S 172 5, 210 21 S 280 31, 318 15 S 384 6, 420 20 S 488 30, 516 14" fill="none" stroke="#E9B9B5" stroke-width="5" stroke-linecap="round"/></svg>"""

ACCENT = "#B5677A"
LAVENDER = "#B9A9D0"


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
          <div class="tape tl"></div><div class="tape pink tr"></div>
          {SPARKLE.format(pos="top:70px;right:70px", s=78, c=ACCENT)}
          {SPARKLE.format(pos="top:150px;right:40px", s=40, c=LAVENDER)}
          {HEART.format(pos="bottom:60px;left:64px", s=64, c=ACCENT)}
          <div class="kicker">＼ {fmt(slide.get("kicker", ""))} ／</div>
          <h1>{fmt(slide["title"])}</h1>
          <div class="sub">{fmt(slide.get("sub", ""))}</div>
        </div>"""
        cls = "cover"
    elif kind == "body":
        note = f'<div class="note">{fmt(slide["note"])}</div>' if slide.get("note") else ""
        inner = f"""
        <div class="content" style="position:relative">
          {SPARKLE.format(pos="top:120px;right:40px", s=70, c=LAVENDER)}
          <div class="num">{fmt(slide.get("num", f"{i:02d}"))}</div>
          <h2>{fmt(slide["title"])}</h2>
          {SQUIGGLE}
          <p>{fmt(slide["text"])}</p>
          {note}
        </div>"""
        cls = "body-slide"
    elif kind == "cta":
        inner = f"""
        <div class="panel">
          <div class="tape pink tl"></div>
          {HEART.format(pos="top:60px;right:64px", s=58, c=ACCENT)}
          {SPARKLE.format(pos="bottom:70px;right:90px", s=52, c=LAVENDER)}
          <div class="lead">{fmt(slide.get("lead", ""))}</div>
          <h2>{fmt(slide["title"])}</h2>
          <div class="keyword-wrap">
            {ARROW.format(pos="left:-170px;top:-40px", c=ACCENT)}
            <div class="keyword">{fmt(slide.get("keyword", ""))}</div>
          </div>
          <div class="small">{fmt(slide.get("small", ""))}</div>
        </div>"""
        cls = "cta"
    else:
        raise ValueError(f"unknown slide type: {kind}")

    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<link rel="stylesheet" href="style.css"></head>
<body class="{cls}">
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
            # style.css とフォントを相対パスで読むため、carousel フォルダに一時ファイルを置く
            with tempfile.NamedTemporaryFile("w", suffix=".html", dir=HERE, delete=False, encoding="utf-8") as f:
                f.write(slide_html(slide, i, len(slides), brand))
                tmp = Path(f.name)
            try:
                page.goto(tmp.as_uri(), wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(300)
                path = out / f"slide_{i + 1:02d}.png"
                page.screenshot(path=str(path), full_page=False)
                paths.append(path)
            finally:
                tmp.unlink(missing_ok=True)
        browser.close()
    return paths


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    for path in render(sys.argv[1], sys.argv[2]):
        print(path)
