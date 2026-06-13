"""PPTist JSON deck renderer.

Converts validated slide data (slideTitle / bullets / speakerNotes) into the
PPTist ``Slide[]`` JSON format that can be loaded into the PPTist editor via
postMessage bridge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


CANVAS_WIDTH = 1000
CANVAS_HEIGHT = 562.5

MARGIN_X = 60
MARGIN_Y = 40
CONTENT_WIDTH = CANVAS_WIDTH - 2 * MARGIN_X


def _uid() -> str:
    return str(uuid4())[:10]


@dataclass(frozen=True)
class ThemeColors:
    background: str = "#FFFFFF"
    primary: str = "#2563EB"
    accent: str = "#3B82F6"
    font_color: str = "#1E293B"
    muted: str = "#64748B"

    @classmethod
    def from_name(cls, name: str) -> ThemeColors:
        presets: dict[str, dict[str, str]] = {
            "engineering-whiteprint": dict(
                background="#FFFFFF", primary="#2563EB", accent="#3B82F6",
                font_color="#1E293B", muted="#64748B",
            ),
            "corporate-clean": dict(
                background="#F8FAFC", primary="#0F766E", accent="#14B8A6",
                font_color="#0F172A", muted="#475569",
            ),
            "academic-paper": dict(
                background="#FFFBEB", primary="#92400E", accent="#D97706",
                font_color="#1C1917", muted="#78716C",
            ),
        }
        return cls(**presets.get(name, presets["engineering-whiteprint"]))


def _text(
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    content_html: str,
    font_name: str = "Microsoft YaHei",
    font_color: str = "#333333",
    rotate: int = 0,
    fill: str | None = None,
    line_height: float = 1.5,
    text_type: str | None = None,
) -> dict[str, Any]:
    el: dict[str, Any] = {
        "type": "text",
        "id": _uid(),
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "content": content_html,
        "rotate": rotate,
        "defaultFontName": font_name,
        "defaultColor": font_color,
        "lineHeight": line_height,
    }
    if fill:
        el["fill"] = fill
    if text_type:
        el["textType"] = text_type
    return el


def _shape(
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    fill: str,
    opacity: float = 1.0,
) -> dict[str, Any]:
    return {
        "type": "shape",
        "id": _uid(),
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "viewBox": [200, 200],
        "path": "M 0 0 L 200 0 L 200 200 L 0 200 Z",
        "fill": fill,
        "fixedRatio": False,
        "rotate": 0,
        "opacity": opacity,
    }


def _html_p(text: str, *, size: int = 18, bold: bool = False, color: str = "#333", align: str = "left") -> str:
    weight = "font-weight:bold;" if bold else ""
    return f"<p style='text-align:{align};'><span style='font-size:{size}px;{weight}color:{color};'>{_escape(text)}</span></p>"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Slide templates
# ---------------------------------------------------------------------------


def _cover(title: str, topic: str, course: str, theme: ThemeColors) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        _shape(left=0, top=0, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, fill=theme.primary, opacity=0.06),
        _shape(left=0, top=0, width=8, height=CANVAS_HEIGHT, fill=theme.primary),
        _text(
            left=MARGIN_X + 20, top=160, width=CONTENT_WIDTH - 20, height=80,
            content_html=_html_p(title, size=36, bold=True, color=theme.font_color),
            font_color=theme.font_color,
            text_type="title",
        ),
        _text(
            left=MARGIN_X + 20, top=260, width=CONTENT_WIDTH - 20, height=50,
            content_html=_html_p(topic, size=20, color=theme.muted),
            font_color=theme.muted,
            text_type="subtitle",
        ),
        _text(
            left=MARGIN_X + 20, top=460, width=CONTENT_WIDTH - 20, height=36,
            content_html=_html_p(course, size=14, color=theme.muted),
            font_color=theme.muted,
        ),
    ]
    return {
        "id": _uid(),
        "elements": elements,
        "background": {"type": "solid", "color": theme.background},
        "type": "cover",
    }


def _agenda(topic: str, slide_titles: list[str], theme: ThemeColors) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        _shape(left=0, top=0, width=CANVAS_WIDTH, height=60, fill=theme.primary, opacity=0.08),
        _text(
            left=MARGIN_X, top=10, width=CONTENT_WIDTH, height=44,
            content_html=_html_p("目录", size=24, bold=True, color=theme.font_color),
            font_color=theme.font_color,
        ),
    ]
    col_width = (CONTENT_WIDTH - 20) / 2
    for i, slide_title in enumerate(slide_titles):
        col = i % 2
        row = i // 2
        x = MARGIN_X + col * (col_width + 20)
        y = 80 + row * 56
        num = f"{i + 1:02d}"
        elements.append(
            _text(
                left=x, top=y, width=col_width, height=48,
                content_html=_html_p(f"{num}  {slide_title}", size=16, color=theme.font_color),
                font_color=theme.font_color,
                fill=theme.primary + "0A",
            ),
        )
    return {
        "id": _uid(),
        "elements": elements,
        "background": {"type": "solid", "color": theme.background},
        "type": "contents",
    }


def _content(
    title: str,
    bullets: list[str],
    speaker_notes: str,
    index: int,
    theme: ThemeColors,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        _shape(left=0, top=0, width=CANVAS_WIDTH, height=4, fill=theme.primary),
        _text(
            left=MARGIN_X, top=20, width=CONTENT_WIDTH, height=52,
            content_html=_html_p(title, size=26, bold=True, color=theme.font_color),
            font_color=theme.font_color,
            text_type="title",
        ),
    ]
    y = 88
    bullet_height = 48
    spacing = 6
    for bullet in bullets[:6]:
        bullet_html = (
            f"<p style='text-align:left;'>"
            f"<span style='font-size:14px;color:{theme.primary};'>●  </span>"
            f"<span style='font-size:16px;color:{theme.font_color};'>{_escape(bullet)}</span>"
            f"</p>"
        )
        elements.append(
            _text(
                left=MARGIN_X + 10, top=y, width=CONTENT_WIDTH - 20, height=bullet_height,
                content_html=bullet_html,
                font_color=theme.font_color,
            ),
        )
        y += bullet_height + spacing

    page_num_html = _html_p(f"{index}", size=12, color=theme.muted, align="right")
    elements.append(
        _text(
            left=CANVAS_WIDTH - 100, top=CANVAS_HEIGHT - 36, width=60, height=24,
            content_html=page_num_html,
            font_color=theme.muted,
        ),
    )

    return {
        "id": _uid(),
        "elements": elements,
        "background": {"type": "solid", "color": theme.background},
        "remark": speaker_notes,
        "type": "content",
    }


def _summary(topic: str, highlights: list[str], theme: ThemeColors) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        _shape(left=0, top=0, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, fill=theme.primary, opacity=0.04),
        _shape(left=0, top=CANVAS_HEIGHT - 4, width=CANVAS_WIDTH, height=4, fill=theme.primary),
        _text(
            left=MARGIN_X, top=60, width=CONTENT_WIDTH, height=56,
            content_html=_html_p("总结", size=30, bold=True, color=theme.font_color, align="center"),
            font_color=theme.font_color,
            text_type="title",
        ),
    ]
    y = 140
    for highlight in highlights[:5]:
        elements.append(
            _text(
                left=MARGIN_X + 60, top=y, width=CONTENT_WIDTH - 120, height=44,
                content_html=_html_p(f"✦  {highlight}", size=16, color=theme.font_color, align="center"),
                font_color=theme.font_color,
            ),
        )
        y += 52

    elements.append(
        _text(
            left=MARGIN_X, top=CANVAS_HEIGHT - 60, width=CONTENT_WIDTH, height=30,
            content_html=_html_p(f"—— {topic} ——", size=13, color=theme.muted, align="center"),
            font_color=theme.muted,
        ),
    )
    return {
        "id": _uid(),
        "elements": elements,
        "background": {"type": "solid", "color": theme.background},
        "type": "end",
    }


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PPTistDeckBuilder:
    theme_name: str = "engineering-whiteprint"

    def render(
        self,
        *,
        title: str,
        topic: str,
        course: str,
        slides: list[dict[str, Any]],
    ) -> str:
        theme = ThemeColors.from_name(self.theme_name)

        pptist_slides: list[dict[str, Any]] = []

        pptist_slides.append(_cover(title=title, topic=topic, course=course, theme=theme))

        content_titles = [s.get("slideTitle", "") for s in slides[1:-1]] if len(slides) > 2 else [s.get("slideTitle", "") for s in slides]
        if content_titles:
            pptist_slides.append(_agenda(topic=topic, slide_titles=content_titles, theme=theme))

        for i, slide_data in enumerate(slides):
            pptist_slides.append(
                _content(
                    title=slide_data.get("slideTitle", f"Slide {i + 1}"),
                    bullets=slide_data.get("bullets", []),
                    speaker_notes=slide_data.get("speakerNotes", ""),
                    index=i + 1,
                    theme=theme,
                ),
            )

        last_bullets = slides[-1].get("bullets", []) if slides else []
        if not last_bullets:
            last_bullets = [s.get("slideTitle", "") for s in slides[:5]]
        pptist_slides.append(_summary(topic=topic, highlights=last_bullets, theme=theme))

        deck = {
            "slides": pptist_slides,
            "theme": {
                "backgroundColor": theme.background,
                "themeColors": [theme.primary, theme.accent, theme.muted, "#ffc000", "#4472c4", "#70ad47"],
                "fontColor": theme.font_color,
                "fontName": "Microsoft YaHei",
                "outline": {"width": 2, "color": "#525252", "style": "solid"},
                "shadow": {"h": 3, "v": 3, "blur": 2, "color": "#808080"},
            },
            "title": title,
        }
        return json.dumps(deck, ensure_ascii=False)
