"""Raster text layout must reproduce PyMuPDF's insert_textbox geometry.

Image templates are pure raster work, but they were filled by wrapping the picture in a
one-page PDF and running the PDF machinery over it — which made them unavailable whenever
PyMuPDF was absent.  raster_text draws straight onto pixels instead.  For existing
templates to keep laying out identically, its wrap + line-height + fit maths must match
PyMuPDF exactly; these tests pin that.
"""
from __future__ import annotations

import pytest
from PIL import Image

from synwevo_docs.engine import fonts
from synwevo_docs.engine.raster_text import RasterFont, draw_box

pytestmark = pytest.mark.skipif(not fonts.cjk_font_file(), reason="本机没有可用的中文字体文件")


@pytest.fixture(scope="module")
def rf():
    return RasterFont()


CASES = [
    ("中文测试自动换行的长句子应该按字符断开", (10, 10, 140, 80), 12),
    ("短文本", (10, 10, 200, 40), 14),
    ("a very long english sentence that must wrap on spaces", (10, 10, 150, 90), 11),
    ("混合 mixed 中英文 text 换行测试", (10, 10, 110, 70), 12),
    ("超长内容需要缩小字号才能放进这个很小的框里面去测试收缩", (10, 10, 90, 40), 16),
    ("line1\nline2\n\nline4", (10, 10, 300, 120), 12),
]


@pytest.mark.parametrize("text,box,size", CASES)
def test_fit_matches_pymupdf(rf, text, box, size):
    """The 'does it fit / how much room is left' answer must equal PyMuPDF's."""
    pymupdf = pytest.importorskip("pymupdf", reason="对照实现不可用")
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=600, height=400)
        page.insert_font(fontname="t", fontbuffer=open(rf.path, "rb").read())
        # render_mode=3 lays the text out invisibly: we want the measurement, not the ink
        expect = page.insert_textbox(pymupdf.Rect(*box), text, fontname="t", fontsize=size, align=0, render_mode=3)
    finally:
        doc.close()
    assert rf.fits(text, box, size) == pytest.approx(expect, abs=0.01), (text, box, size)


def test_cjk_wraps_by_character_and_never_overflows(rf):
    text = "中文测试自动换行的长句子应该按字符断开因为中文没有空格"
    width, size = 120, 12
    lines = rf.wrap(text, width, size)
    assert len(lines) > 1, "长中文必须换行"
    for ln in lines:
        assert rf.width(ln, size) <= width + 0.01, f"行超出框宽：{ln!r}"
    assert "".join(lines) == text, "换行不能丢字或加字"


def test_english_wraps_on_spaces_not_mid_word(rf):
    lines = rf.wrap("alpha bravo charlie delta echo foxtrot", 120, 12)
    for ln in lines:
        for word in ln.split():
            assert word in "alpha bravo charlie delta echo foxtrot", f"单词被切断：{word!r}"


def test_explicit_newlines_and_blank_lines_survive(rf):
    assert rf.wrap("a\nb\n\nc", 300, 12) == ["a", "b", "", "c"]


def test_shrink_to_fit_stops_at_the_floor_and_reports_overflow(rf):
    im = Image.new("RGB", (200, 60), "white")
    r = draw_box(im, (5, 5, 60, 25), "这段文字远远超过这个小框能容纳的范围无论如何都放不下",
                 font=rf, font_size=16, min_font_size=12)
    assert r["ok"] is False and r["overflow"] is True
    assert r["font_size"] == 12, "不能缩到低于下限"
    assert im.tobytes() == Image.new("RGB", (200, 60), "white").tobytes(), "放不下时不应画任何东西"


def test_draw_only_touches_the_box_and_leaves_other_pixels_exact(rf):
    im = Image.new("RGB", (240, 120), (250, 248, 240))
    before = im.copy()
    box = (40, 30, 200, 80)
    r = draw_box(im, box, "订单号 DD-001", font=rf, font_size=14, min_font_size=8, color=(0, 0, 0))
    assert r["ok"] and r["lines"]
    diff = [(x, y) for x in range(im.width) for y in range(im.height)
            if im.getpixel((x, y)) != before.getpixel((x, y))]
    assert diff, "应当真的画上了文字"
    for x, y in diff:
        assert box[0] <= x <= box[2] and box[1] <= y <= box[3], f"像素 {(x, y)} 落在框外"


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_rotation_keeps_ink_inside_the_box(rf, rot):
    im = Image.new("RGB", (200, 200), "white")
    box = (50, 50, 150, 120)
    r = draw_box(im, box, "中文 ABC", font=rf, font_size=12, min_font_size=8, rotate=rot)
    assert r["ok"], rot
    for x in range(im.width):
        for y in range(im.height):
            if im.getpixel((x, y)) != (255, 255, 255):
                assert box[0] - 1 <= x <= box[2] + 1 and box[1] - 1 <= y <= box[3] + 1, (rot, x, y)


def test_missing_font_raises_an_actionable_error(monkeypatch):
    monkeypatch.setattr(fonts, "cjk_font_file", lambda: None)
    with pytest.raises(fonts.NoCJKFont) as e:
        fonts.require_cjk_font()
    assert "SYNWEVO_DOCS_CJK_FONT_FILE" in str(e.value) and "文件路径" in str(e.value)


def test_drawn_width_matches_measured_width_at_fractional_sizes():
    """The shrink ladder lands on fractions, and a box is cleared to exactly the measured width.

    RasterFont used to measure at the exact float size but rasterise at int(round(size)), so at
    13.5 it measured 189px, drew 196px, and overran the box it had just reported fitting — text
    clipped in a delivered document, with nothing in the result saying so.
    """
    f = RasterFont()
    text = "示例收货方名称示例收货方名称"
    for size in (9.7, 11.25, 12.0, 13.5, 17.3):
        measured = f.width(text, size)
        drawn = f._pil(size).getlength(text)
        assert abs(drawn - measured) <= 0.5, (size, measured, drawn)


def test_drawn_ink_is_as_wide_as_it_was_measured_to_be():
    """The overflow does not spill — draw_box's mask is sized to the layout, so oversized glyphs
    are cut off at the edge instead. That makes the damage invisible in the result dict: the fill
    reports ok, and the document quietly ends up with the tail of a value missing.

    So the property to pin is that the ink really is the width it was measured as. At 13.5 the
    old int-rounded font drew 196px of ink for 189px of measurement."""
    from PIL import Image
    f = RasterFont()
    text = "示例收货方名称示例收货方名称"      # long enough that a 0.5px/char error is unambiguous
    size = 13.5
    measured = f.width(text, size)
    box = (20, 20, 20 + int(measured) + 80, 20 + int(f.line_height(size)) + 20)   # generous: no clipping
    im = Image.new("RGB", (box[2] + 40, box[3] + 40), (255, 255, 255))
    assert draw_box(im, box, text, font=f, font_size=size, min_font_size=6, color=(0, 0, 0))["ok"]
    ink = im.convert("L").point(lambda v: 255 - v).getbbox()
    assert ink is not None
    assert abs((ink[2] - ink[0]) - measured) <= 3, (ink[2] - ink[0], measured)
