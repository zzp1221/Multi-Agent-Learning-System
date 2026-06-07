"""HTML deck renderer backed by the vendored html-ppt skill assets."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any


ASSET_DIR = Path(__file__).resolve().parent / "html_ppt_assets"
DEFAULT_BRAND = "\u667a\u5b66\u5f15\u64ce"


@dataclass(frozen=True)
class HtmlPptDeckBuilder:
    """Build a single-file HTML presentation from validated slide data."""

    assets_dir: Path = ASSET_DIR
    theme_name: str = "engineering-whiteprint"

    def render(
        self,
        *,
        title: str,
        topic: str,
        course: str,
        slides: list[dict[str, Any]],
    ) -> str:
        content_slides = [self._render_content_slide(index, slide, topic) for index, slide in enumerate(slides, start=1)]
        all_slides = [
            self._render_cover(title=title, topic=topic, course=course),
            self._render_agenda(topic=topic, slides=slides),
            *content_slides,
            self._render_summary(topic=topic, slides=slides),
        ]
        total = len(all_slides)
        body = "\n\n".join(
            self._with_footer(slide_html, index=index, total=total, course=course)
            for index, slide_html in enumerate(all_slides, start=1)
        )
        return self._render_document(title=title, body=body)

    def _render_document(self, *, title: str, body: str) -> str:
        css = "\n".join(
            [
                self._read_asset("base.css"),
                self._read_asset(f"themes/{self.theme_name}.css"),
                self._read_asset("animations.css"),
                self._custom_css(),
            ]
        )
        runtime = self._escape_script_block(
            self._read_asset("runtime.js").replace(
                "<script src=\"../assets/runtime.js\"></script>",
                "",
            )
        )
        safe_title = escape(title)
        return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="{escape(self.theme_name)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src data:; frame-src 'self' data: blob:; child-src 'self' data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none'">
<title>{safe_title}</title>
<style>
{css}
</style>
</head>
<body>
<div class="deck" data-generated-by="zhixue-html-ppt">
{body}
</div>
<script>
{runtime}
</script>
</body>
</html>
"""

    def _render_cover(self, *, title: str, topic: str, course: str) -> str:
        safe_title = escape(title)
        safe_topic = escape(topic)
        safe_course = escape(course or "\u667a\u5b66\u5f15\u64ce")
        return f"""<section class="slide slide-cover center" data-title="{safe_title}">
  <div class="cover-mark">\u667a\u5b66\u5f15\u64ce</div>
  <div class="cover-copy">
    <p class="kicker">{safe_course}</p>
    <h1 class="h1 anim-rise-in" data-anim="rise-in">{safe_title}</h1>
    <p class="lede">{safe_topic}</p>
  </div>
  <div class="notes">\u5f00\u573a\u65f6\u5148\u8bf4\u660e\u8fd9\u4efd\u8bfe\u4ef6\u7684\u5b66\u4e60\u4e3b\u9898\u662f {safe_topic}\uff0c\u5e76\u63d0\u9192\u5b66\u751f\u5173\u6ce8\u6982\u5ff5\u3001\u5224\u65ad\u65b9\u6cd5\u548c\u5e94\u7528\u8fb9\u754c\u3002</div>
</section>"""

    def _render_agenda(self, *, topic: str, slides: list[dict[str, Any]]) -> str:
        items = "\n".join(
            f"""    <li><span>{index:02d}</span><strong>{escape(str(slide.get("slideTitle") or slide.get("title") or ""))}</strong></li>"""
            for index, slide in enumerate(slides, start=1)
        )
        return f"""<section class="slide slide-agenda" data-title="\u5b66\u4e60\u8def\u7ebf">
  <p class="kicker">\u5b66\u4e60\u8def\u7ebf</p>
  <h2 class="h2">{escape(topic)} \u7684\u5173\u952e\u6a21\u5757</h2>
  <ol class="agenda-list anim-stagger-list" data-anim-target>
{items}
  </ol>
  <div class="notes">\u672c\u9875\u6309\u987a\u5e8f\u9884\u544a\u6574\u4efd\u8bfe\u4ef6\u7ed3\u6784\uff0c\u8ba9\u5b66\u751f\u5148\u5efa\u7acb\u5b66\u4e60\u8def\u7ebf\u3002</div>
</section>"""

    def _render_content_slide(self, index: int, slide: dict[str, Any], topic: str) -> str:
        title = str(slide.get("slideTitle") or slide.get("title") or f"Slide {index}").strip()
        bullets = [str(item).strip() for item in slide.get("bullets", []) if str(item).strip()]
        notes = str(slide.get("speakerNotes") or slide.get("speaker_notes") or "").strip()
        bullet_cards = "\n".join(
            f"""    <div class="card bullet-card"><span class="pill">{bullet_index:02d}</span><p>{escape(bullet)}</p></div>"""
            for bullet_index, bullet in enumerate(bullets, start=1)
        )
        safe_title = escape(title)
        return f"""<section class="slide slide-content" data-title="{safe_title}">
  <p class="kicker">{escape(topic)} / {index:02d}</p>
  <h2 class="h2">{safe_title}</h2>
  <div class="grid bullet-grid anim-stagger-list mt-l" data-anim-target>
{bullet_cards}
  </div>
  <div class="notes">{escape(notes)}</div>
</section>"""

    def _render_summary(self, *, topic: str, slides: list[dict[str, Any]]) -> str:
        titles = [str(slide.get("slideTitle") or slide.get("title") or "").strip() for slide in slides[:4]]
        summary_items = "\n".join(f"    <li>{escape(title)}</li>" for title in titles if title)
        return f"""<section class="slide slide-summary center" data-title="\u603b\u7ed3\u4e0e\u4e0b\u4e00\u6b65">
  <div class="summary-panel">
    <p class="kicker">\u603b\u7ed3\u4e0e\u4e0b\u4e00\u6b65</p>
    <h2 class="h2">{escape(topic)}</h2>
    <ul class="summary-list">
{summary_items}
    </ul>
    <p class="lede">\u8bf7\u7528\u4e00\u9053\u5c0f\u9898\u6216\u4e00\u4e2a\u771f\u5b9e\u573a\u666f\uff0c\u590d\u8ff0\u5b83\u7684\u9002\u7528\u6761\u4ef6\u548c\u5e38\u89c1\u8bef\u533a\u3002</p>
  </div>
  <div class="notes">\u7ed3\u5c3e\u65f6\u4e0d\u8981\u91cd\u590d\u6240\u6709\u7ec6\u8282\uff0c\u800c\u662f\u8ba9\u5b66\u751f\u7528\u81ea\u5df1\u7684\u8bed\u8a00\u8bf4\u51fa\u4e3b\u9898\u7684\u5224\u65ad\u65b9\u6cd5\u3001\u4f7f\u7528\u8fb9\u754c\u548c\u4e0b\u4e00\u6b65\u7ec3\u4e60\u3002</div>
</section>"""

    def _with_footer(self, slide_html: str, *, index: int, total: int, course: str) -> str:
        safe_course = escape(course or DEFAULT_BRAND)
        footer = (
            f'  <div class="deck-footer"><span>{safe_course}</span>'
            f'<span>{index} / {total}</span></div>\n'
        )
        return slide_html.replace("</section>", footer + "</section>", 1)

    def _read_asset(self, relative_path: str) -> str:
        path = self.assets_dir / relative_path
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _escape_script_block(script: str) -> str:
        return script.replace("</script>", "<\\/script>")

    @staticmethod
    def _custom_css() -> str:
        return """
:root {
  --letter-tight: 0;
  --letter-normal: 0;
}
body {
  text-wrap: pretty;
}
.slide {
  padding: 70px 86px;
}
.slide-cover {
  text-align: left;
  justify-content: center;
}
.cover-copy {
  width: min(980px, 92%);
}
.cover-mark {
  position: absolute;
  right: 76px;
  top: 62px;
  font-family: var(--font-mono);
  font-size: 16px;
  letter-spacing: .12em;
  color: var(--accent-3);
  border: 1px solid var(--border-strong);
  padding: 8px 14px;
  background: var(--surface);
}
.slide-agenda .h2,
.slide-content .h2 {
  max-width: 980px;
}
.agenda-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 22px;
  padding: 0;
  margin: 32px 0 0;
  list-style: none;
}
.agenda-list li {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr);
  align-items: start;
  min-height: 58px;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
}
.agenda-list span,
.bullet-card .pill {
  font-family: var(--font-mono);
}
.agenda-list strong {
  font-size: 18px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.bullet-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.bullet-card {
  min-height: 180px;
  border-radius: 0;
}
.bullet-card p {
  margin: 18px 0 0;
  font-size: 22px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.summary-panel {
  width: min(980px, 90%);
  text-align: left;
}
.summary-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 20px;
  padding: 0;
  margin: 28px 0;
  list-style: none;
}
.summary-list li {
  padding: 14px 16px;
  border-left: 3px solid var(--accent-3);
  background: var(--surface);
  border-top: 1px solid var(--border);
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  font-size: 18px;
  overflow-wrap: anywhere;
}
@media (max-width: 900px) {
  .slide {
    padding: 42px 28px;
  }
  h1.title, .h1 {
    font-size: 44px;
  }
  h2.title, .h2 {
    font-size: 34px;
  }
  .agenda-list,
  .bullet-grid,
  .summary-list {
    grid-template-columns: 1fr;
  }
  .bullet-card {
    min-height: 120px;
  }
}
"""
