# Copyright (c) 2026 Otaku Solutions
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""The motion pieces: each one is a small piece of HTML animated with plain CSS.

Every piece is drawn for one frame size and one length. All the pieces of a request
are laid end to end on one "sheet" (piece 1 from 0 s, piece 2 right after it, and so
on), so the whole sheet renders in one browser session. motion.py then cuts each piece
back out of the sheet and lays it over the video at its real time.

Plain CSS keyframes only (no GSAP or other animation library): HyperFrames seeks CSS
animations frame by frame. Each piece's animations are delayed by --t0, the time the
piece starts on the sheet, so the same CSS works wherever the piece sits.
"""

import html
import re
from urllib.parse import quote

# type -> (default length in seconds, sound it plays by default, shortest, longest)
PIECES = {
    "lower_third": (4.0, "slide", 2.0, 10.0),
    "title": (2.8, "impact", 1.2, 8.0),
    "keyword": (1.2, "pop", 0.5, 4.0),
    "stat": (3.0, "ding", 1.5, 8.0),
    "chapter": (2.4, "whoosh", 1.2, 6.0),
    "follow": (4.0, "pop", 2.0, 10.0),
    "quote": (4.5, "thud", 2.0, 12.0),
    "transition": (0.7, "whoosh", 0.4, 1.5),
    "logo": (3.0, "ding", 1.0, 3600.0),
    # Made to sit behind the speaker ("layer": "behind"), though they work in front too.
    "big_text": (3.0, "impact", 1.0, 3600.0),
    "graph": (3.5, "riser", 2.0, 10.0),
    "doodle": (2.5, "pop", 1.0, 3600.0),
    # Sits in front, at the bottom.
    "checklist": (4.0, "tick", 2.0, 12.0),
}

# Pieces that need words in "title".
NEEDS_TITLE = ("lower_third", "title", "keyword", "stat", "chapter", "follow", "quote", "big_text")

# Hand-drawn doodles: SVG paths on a 100 x 100 box, drawn on and then wobbling.
DOODLES = {
    "crown": "M14 72 L20 30 L38 52 L50 22 L62 52 L80 30 L86 72 Z M16 82 L84 82",
    "star": "M50 10 L61 38 L91 39 L67 58 L76 88 L50 70 L24 88 L33 58 L9 39 L39 38 Z",
    "heart": "M50 86 C18 62 8 44 18 28 C28 13 44 16 50 32 C56 16 72 13 82 28 C92 44 82 62 50 86 Z",
    "arrow": "M12 80 C30 52 50 40 80 30 M62 22 L82 29 L72 48",
    "burst": "M50 8 L50 26 M50 74 L50 92 M8 50 L26 50 M74 50 L92 50 M20 20 L33 33 M67 67 L80 80 M80 20 L67 33 M33 67 L20 80",
    "circle": "M50 12 C80 10 92 34 88 56 C84 80 58 92 36 86 C14 80 6 56 14 36 C20 22 34 14 56 14",
}

LOOKS = ("bold", "clean", "editorial", "neon")

# Font used when the request names none.
LOOK_FONTS = {"bold": "Roboto", "clean": "Roboto", "editorial": "Libre Baskerville", "neon": "Roboto"}

COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT = re.compile(r"^[A-Za-z0-9 ._-]{1,60}$")
NUMBER = re.compile(r"^([^\d]{0,3})(\d{1,9})([^\d]{0,12})$")


def esc(text):
    return html.escape(str(text or ""), quote=True)


def _counter(title):
    """A plain whole number (with up to 3 characters before and 12 after) can count up."""
    match = NUMBER.match(title.strip())
    if not match:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN"]
TICK = '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="#121316" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>'


def svg_image(body, view="0 0 100 100", fit="xMidYMid meet", cls=""):
    """An inline SVG drawing as an <img>. HyperFrames only draws inline <svg> in the first
    piece of a sheet, so drawings are pictures and are revealed with CSS on the <img>."""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" preserveAspectRatio="{fit}">{body}</svg>'
    return f'<img class="{cls}" alt="" src="data:image/svg+xml;utf8,{quote(svg)}">'


def big_text_size(words, aspect):
    """Font size of a big_text piece in --u units: the word fills the frame's width like a
    magazine's name across its cover, so the longer the word, the smaller."""
    return min(46.0, 128.0 * aspect / max(3, len(words.strip())))


def piece_html(index, piece, t0):
    """The HTML of one piece, starting `t0` seconds into the sheet."""
    kind = piece["type"]
    d = piece["duration"]
    title = esc(piece.get("title"))
    sub = esc(piece.get("subtitle"))
    # HyperFrames plays a clip's CSS animations from the clip's own start, so delays count
    # from the piece's start (--t0 stays 0); data-start places it on the sheet.
    style = f'style="--t0:0s;--d:{d:.3f}s"'
    clip = f'class="clip p p-{kind}" data-start="{t0:.3f}" data-duration="{d:.3f}" data-track-index="{index + 1}" {style}'

    if kind == "lower_third":
        inner = f'<div class="lt"><div class="lt-name a-up">{title}</div>'
        if sub:
            inner += f'<div class="lt-role a-up2">{sub}</div>'
        inner += '<div class="lt-bar a-grow"></div></div>'
    elif kind == "title":
        inner = '<div class="tc">'
        if sub:
            inner += f'<div class="tc-kick a-up">{sub}</div>'
        inner += f'<div class="tc-head a-rise">{title}</div></div>'
    elif kind == "keyword":
        inner = f'<div class="kw"><div class="kw-word a-slam">{title}</div></div>'
    elif kind == "stat":
        counter = _counter(piece.get("title") or "")
        if counter:
            before, number, after = counter
            big = (
                f'<div class="st-big a-pop">{esc(before)}<span class="st-n" style="--to:{number}"></span>{esc(after)}</div>'
            )
        else:
            big = f'<div class="st-big a-pop">{title}</div>'
        inner = f'<div class="st">{big}'
        if sub:
            inner += f'<div class="st-label a-up2">{sub}</div>'
        inner += "</div>"
    elif kind == "chapter":
        inner = '<div class="ch"><div class="ch-wipe a-wipe"></div><div class="ch-text">'
        if sub:
            inner += f'<div class="ch-kick a-up">{sub}</div>'
        inner += f'<div class="ch-title a-up2">{title}</div></div></div>'
    elif kind == "follow":
        inner = '<div class="fo a-card"><div class="fo-dot"></div><div>'
        inner += f'<div class="fo-handle">{title}</div>'
        if sub:
            inner += f'<div class="fo-cta">{sub}</div>'
        inner += '</div><div class="fo-btn">Follow</div></div>'
    elif kind == "quote":
        inner = f'<div class="qt"><div class="qt-mark a-pop">&ldquo;</div><div class="qt-text a-up">{title}</div>'
        if sub:
            inner += f'<div class="qt-who a-up2">{sub}</div>'
        inner += "</div>"
    elif kind == "transition":
        inner = '<div class="tr a-sweep"></div><div class="tr tr2 a-sweep2"></div>'
    elif kind == "logo":
        inner = f'<div class="lg a-pop"><img src="{esc(piece.get("image"))}" alt=""></div>'
    elif kind == "big_text":
        words = (piece.get("title") or "").strip()
        # Fills the frame's width: the longer the word, the smaller it is drawn.
        size = big_text_size(words, piece.get("_aspect", 1.0))
        top = f"top:{float(piece['y']):.1f}%;" if piece.get("y") is not None else ""
        inner = f'<div class="bt" style="--fs:{size:.2f};{top}">'
        if sub:
            inner += f'<div class="bt-kick a-up">{sub}</div>'
        inner += f'<div class="bt-word a-rise"><span>{title}</span></div></div>'
    elif kind == "graph":
        # Months along the top, and a thin line that creeps along, then shoots up to the
        # tag: the "growth chart behind my head" look.
        labels = [esc(x) for x in (piece.get("items") or [])][:6] or MONTHS
        accent = piece.get("_accent", "#46e5b7")
        pts = []
        for i in range(13):
            x = 2 + i * 96 / 12
            y = 88 - 80 * (i / 12) ** 2.6
            pts.append((x, y))
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        area = f"2,100 {line} {pts[-1][0]:.1f},100"
        body = (
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{accent}" stop-opacity=".32"/>'
            f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="{area}" fill="url(#g)"/>'
            f'<polyline points="{line}" fill="none" stroke="{accent}" stroke-width="2.4" vector-effect="non-scaling-stroke" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )
        inner = '<div class="gr"><div class="gr-labels">' + "".join(
            f'<span class="a-up" style="animation-delay:{0.05 + i * 0.07:.2f}s,calc(var(--d) - .34s)">{lab}</span>'
            for i, lab in enumerate(labels)
        ) + "</div>"
        inner += '<div class="gr-plot">' + svg_image(body, fit="none", cls="gr-line a-wipe2")
        if title:
            inner += f'<div class="gr-tag a-pop2">{title}</div>'
        inner += "</div></div>"
    elif kind == "doodle":
        shape = piece.get("shape") if piece.get("shape") in DOODLES else "star"
        x = min(95.0, max(5.0, float(piece.get("x", 50))))
        y = min(95.0, max(5.0, float(piece.get("y", 12))))
        path = (
            f'<path d="{DOODLES[shape]}" fill="none" stroke="{piece.get("_accent", "#46e5b7")}" stroke-width="4" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )
        inner = f'<div class="dd" style="left:{x:.1f}%;top:{y:.1f}%"><div class="dd-boil">'
        inner += svg_image(path, view="-5 -5 110 110", cls="a-scribble") + "</div></div>"
    elif kind == "checklist":
        # A dark glass card: a header with a count, round boxes, and each line typed in
        # and ticked in turn while the lines still to come wait as grey bars.
        raw_items = [x for x in (piece.get("items") or [])][:6] or [piece.get("title") or ""]
        heading = title if piece.get("items") else ""
        step = min(0.9, max(0.3, (d - 1.0) / max(1, len(raw_items))))
        rows, ticks = [], []
        for i, item in enumerate(raw_items):
            at = 0.35 + i * step
            typing = min(step * 0.7, 0.03 * len(item) + 0.1)
            ticks.append(at + typing)
            rows.append(
                f'<div class="ck-row" style="--at:{at:.2f}s;--type:{typing:.2f}s;--chars:{max(1, len(item))}">'
                '<span class="ck-box">' + svg_image(TICK, view="0 0 24 24", cls="ck-mark") + "</span>"
                f'<span class="ck-slot"><span class="ck-skel"></span><span class="ck-text">{esc(item)}</span></span></div>'
            )
        total = len(raw_items)
        # "0 of 3", "1 of 3"... each shown from one tick to the next.
        edges = [0.0, *ticks, 999.0]
        count = "".join(
            f'<span class="ck-n" style="--on:{edges[k]:.2f}s;--len:{edges[k + 1] - edges[k]:.2f}s">{k} of {total}</span>'
            for k in range(total + 1)
        )
        bar = "".join(f'<span class="ck-seg" style="--on:{t:.2f}s"></span>' for t in ticks)
        inner = f'<div class="ck a-card"><div class="ck-head"><span class="ck-title">{heading}</span><span class="ck-count">{count}</span></div>'
        inner += f'<div class="ck-bar">{bar}</div>' + "".join(rows) + "</div>"
    else:  # validated before this point
        raise ValueError(f"Unknown piece type: {kind}")
    return f"<div {clip}>{inner}</div>"


def sheet_html(pieces, width, height, style, total):
    """One composition with every piece laid end to end."""
    look = style.get("look") if style.get("look") in LOOKS else "bold"
    accent = style.get("accent") if COLOR.match(style.get("accent") or "") else "#46e5b7"
    text = style.get("text") if COLOR.match(style.get("text") or "") else "#ffffff"
    font = style.get("font") if FONT.match(style.get("font") or "") else LOOK_FONTS[look]
    tall = height > width
    unit = min(width, height) / 100.0  # 1u = 1% of the short side

    body = []
    t = 0.0
    for i, piece in enumerate(pieces):
        body.append(piece_html(i, dict(piece, _aspect=width / min(width, height), _accent=accent), t))
        t += piece["duration"]

    css = CSS.replace("__U__", f"{unit:.3f}px")
    return f"""<!doctype html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width={width}, height={height}">
<style>
:root{{--accent:{accent};--text:{text};--ink:#121316;--font:"{esc(font)}";--lt-bottom:{"27%" if tall else "11%"};--side:{"7%" if tall else "5%"}}}
html,body{{width:{width}px;height:{height}px}}
#root{{width:{width}px;height:{height}px}}
{css}
</style></head><body>
<div id="root" class="look-{look}{" tall" if tall else ""}" data-composition-id="main" data-start="0" data-duration="{total:.3f}" data-width="{width}" data-height="{height}">
{"".join(body)}
</div></body></html>
"""


# --u is 1% of the short side, so every piece keeps its proportions at any frame size.
CSS = """
@property --n{syntax:'<integer>';inherits:false;initial-value:0}
*{margin:0;padding:0;box-sizing:border-box}
html,body{overflow:hidden;background:transparent}
#root{position:relative;--u:__U__;font-family:var(--font),sans-serif;color:var(--text)}
.p{position:absolute;inset:0}
.look-bold,.look-neon{--w:900;--case:uppercase;--track:-.01em}
.look-clean{--w:600;--case:none;--track:0}
.look-editorial{--w:700;--case:none;--track:0}
.look-neon{--glow:0 0 calc(var(--u)*1.2) var(--accent),0 0 calc(var(--u)*3) var(--accent)}

/* timing: in at --t0, out just before the piece ends */
.a-up{animation:up .5s cubic-bezier(.2,.8,.2,1) calc(var(--t0) + .12s) both,fade .3s ease-in calc(var(--t0) + var(--d) - .38s) forwards}
.a-up2{animation:up .5s cubic-bezier(.2,.8,.2,1) calc(var(--t0) + .26s) both,fade .3s ease-in calc(var(--t0) + var(--d) - .34s) forwards}
.a-rise{animation:rise .6s cubic-bezier(.16,1,.3,1) calc(var(--t0) + .05s) both,fade .3s ease-in calc(var(--t0) + var(--d) - .32s) forwards}
.a-grow{transform-origin:0 50%;animation:grow .45s cubic-bezier(.2,.8,.2,1) calc(var(--t0) + .05s) both,shrink .3s ease-in calc(var(--t0) + var(--d) - .3s) forwards}
.a-slam{animation:slam .32s cubic-bezier(.2,1.4,.4,1) calc(var(--t0)) both,fade .2s ease-in calc(var(--t0) + var(--d) - .2s) forwards}
.a-pop{animation:pop .45s cubic-bezier(.2,1.5,.4,1) calc(var(--t0) + .05s) both,fade .3s ease-in calc(var(--t0) + var(--d) - .3s) forwards}
.a-card{animation:card .55s cubic-bezier(.2,1.2,.4,1) calc(var(--t0) + .05s) both,cardout .35s ease-in calc(var(--t0) + var(--d) - .35s) forwards}
.a-wipe{transform-origin:0 50%;animation:grow .4s cubic-bezier(.7,0,.2,1) calc(var(--t0)) both,wipeout .4s cubic-bezier(.7,0,.2,1) calc(var(--t0) + var(--d) - .4s) forwards}
.a-sweep{animation:sweep var(--d) cubic-bezier(.7,0,.3,1) calc(var(--t0)) both}
.a-sweep2{animation:sweep var(--d) cubic-bezier(.7,0,.3,1) calc(var(--t0) + var(--d) * .12) both}
.st-n{animation:count .9s cubic-bezier(.2,.8,.2,1) calc(var(--t0) + .1s) both;counter-reset:n var(--n)}
.st-n::after{content:counter(n)}

@keyframes up{from{transform:translateY(calc(var(--u)*4));opacity:0;clip-path:inset(0 0 100% 0)}to{transform:none;opacity:1;clip-path:inset(0)}}
@keyframes rise{from{transform:translateY(calc(var(--u)*6)) scale(.96);opacity:0;filter:blur(calc(var(--u)*.6))}to{transform:none;opacity:1;filter:none}}
@keyframes fade{to{opacity:0;transform:translateY(calc(var(--u)*-1.5))}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes shrink{to{transform:scaleX(0);transform-origin:100% 50%}}
@keyframes slam{from{transform:scale(2.4);opacity:0}60%{opacity:1}to{transform:scale(1);opacity:1}}
@keyframes pop{from{transform:scale(.4);opacity:0}to{transform:scale(1);opacity:1}}
@keyframes card{from{transform:translateY(calc(var(--u)*14)) scale(.9);opacity:0}to{transform:none;opacity:1}}
@keyframes cardout{to{transform:translateY(calc(var(--u)*14)) scale(.9);opacity:0}}
@keyframes wipeout{from{transform:scaleX(1);transform-origin:100% 50%}to{transform:scaleX(0);transform-origin:100% 50%}}
@keyframes sweep{from{transform:translateX(-105%) skewX(-12deg)}to{transform:translateX(105%) skewX(-12deg)}}
@keyframes count{from{--n:0}to{--n:var(--to)}}

/* lower third */
.lt{position:absolute;left:var(--side);bottom:var(--lt-bottom);display:flex;flex-direction:column;align-items:flex-start;gap:calc(var(--u)*1.1)}
.lt-name{font-weight:var(--w);font-size:calc(var(--u)*6);line-height:1;letter-spacing:var(--track);text-shadow:0 calc(var(--u)*.2) calc(var(--u)*2) rgba(0,0,0,.55),var(--glow,0 0 0 transparent);white-space:nowrap}
.lt-role{font-weight:700;font-size:calc(var(--u)*2.6);letter-spacing:.08em;text-transform:uppercase;background:var(--accent);color:var(--ink);padding:calc(var(--u)*.6) calc(var(--u)*1.3)}
.lt-bar{height:calc(var(--u)*.55);width:100%;min-width:calc(var(--u)*20);background:var(--accent);border-radius:calc(var(--u)*.3)}
.look-clean .lt-role{background:rgba(18,19,22,.72);color:var(--text);letter-spacing:.04em}
.look-editorial .lt-role{background:none;color:var(--text);padding:0;font-style:italic;text-transform:none;letter-spacing:0}

/* title card */
.tc{position:absolute;left:var(--side);right:var(--side);top:30%;display:flex;flex-direction:column;align-items:center;gap:calc(var(--u)*2);text-align:center}
.tall .tc{top:14%}
.tc-kick{font-weight:700;font-size:calc(var(--u)*2.6);letter-spacing:.12em;text-transform:uppercase;background:var(--accent);color:var(--ink);padding:calc(var(--u)*.7) calc(var(--u)*1.6)}
.tc-head{font-weight:var(--w);font-size:calc(var(--u)*8.5);line-height:1.02;letter-spacing:var(--track);text-transform:var(--case);text-shadow:0 calc(var(--u)*.4) calc(var(--u)*3) rgba(0,0,0,.55),var(--glow,0 0 0 transparent)}
.look-editorial .tc-head{font-style:italic}

/* keyword slam */
.kw{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:0 var(--side)}
.kw-word{font-weight:900;font-size:calc(var(--u)*13);line-height:1;text-transform:uppercase;text-align:center;color:var(--text);-webkit-text-stroke:calc(var(--u)*.35) var(--ink);paint-order:stroke fill;text-shadow:0 calc(var(--u)*.6) 0 var(--accent),var(--glow,0 0 0 transparent)}
.look-clean .kw-word,.look-editorial .kw-word{-webkit-text-stroke:0;text-shadow:0 calc(var(--u)*.3) calc(var(--u)*2.5) rgba(0,0,0,.6)}

/* stat card */
.st{position:absolute;right:var(--side);top:24%;background:rgba(18,19,22,.86);border-left:calc(var(--u)*.7) solid var(--accent);padding:calc(var(--u)*2) calc(var(--u)*2.6);display:flex;flex-direction:column;gap:calc(var(--u)*.6);max-width:60%}
.tall .st{top:20%}
.st-big{font-weight:900;font-size:calc(var(--u)*8);line-height:1;color:var(--accent);font-variant-numeric:tabular-nums;text-shadow:var(--glow,0 0 0 transparent);transform-origin:0 50%}
.st-label{font-weight:600;font-size:calc(var(--u)*2.8);color:var(--text)}

/* chapter card */
.ch{position:absolute;left:0;right:0;top:40%;height:calc(var(--u)*18)}
.tall .ch{top:36%}
.ch-wipe{position:absolute;inset:0;background:var(--accent)}
.ch-text{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 var(--side);gap:calc(var(--u)*.8);color:var(--ink)}
.ch-kick{font-weight:700;font-size:calc(var(--u)*2.4);letter-spacing:.14em;text-transform:uppercase}
.ch-title{font-weight:var(--w);font-size:calc(var(--u)*6.5);line-height:1;letter-spacing:var(--track);text-transform:var(--case)}

/* follow card */
.fo{position:absolute;left:50%;bottom:var(--lt-bottom);margin-left:calc(var(--u)*-34);width:calc(var(--u)*68);display:flex;align-items:center;gap:calc(var(--u)*2);background:#fff;color:var(--ink);border-radius:calc(var(--u)*2.2);padding:calc(var(--u)*1.8) calc(var(--u)*2.2);box-shadow:0 calc(var(--u)*1) calc(var(--u)*4) rgba(0,0,0,.35)}
.fo-dot{flex:none;width:calc(var(--u)*8);height:calc(var(--u)*8);border-radius:50%;background:var(--accent)}
.fo>div:nth-child(2){flex:1;min-width:0}
.fo-handle{font-weight:800;font-size:calc(var(--u)*3.6);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fo-cta{font-weight:500;font-size:calc(var(--u)*2.3);opacity:.7}
.fo-btn{flex:none;background:var(--accent);color:var(--ink);font-weight:800;font-size:calc(var(--u)*2.6);padding:calc(var(--u)*1.2) calc(var(--u)*2.4);border-radius:calc(var(--u)*5)}

/* quote */
.qt{position:absolute;left:var(--side);right:var(--side);top:22%;display:flex;flex-direction:column;gap:calc(var(--u)*1.4);background:rgba(18,19,22,.78);padding:calc(var(--u)*3);border-radius:calc(var(--u)*1)}
.qt-mark{font-family:"Libre Baskerville",serif;font-size:calc(var(--u)*12);line-height:.6;height:calc(var(--u)*5);color:var(--accent);transform-origin:0 0}
.qt-text{font-weight:600;font-size:calc(var(--u)*4.4);line-height:1.2}
.look-editorial .qt-text{font-style:italic}
.qt-who{font-weight:700;font-size:calc(var(--u)*2.4);letter-spacing:.1em;text-transform:uppercase;color:var(--accent)}

/* transition */
.tr{position:absolute;top:-10%;bottom:-10%;left:-20%;right:-20%;background:var(--accent)}
.tr2{background:var(--ink);opacity:.9}

/* big text, made to sit behind the speaker */
.a-scribble{animation:scribble .6s steps(6) calc(var(--t0) + .05s) both,fade .3s ease-in calc(var(--t0) + var(--d) - .3s) forwards}
@keyframes scribble{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@keyframes reveal{from{clip-path:inset(-10% 100% -10% -10%)}to{clip-path:inset(-10% -10% -10% -10%)}}
@keyframes boil{0%{transform:rotate(-3deg) scale(1)}33%{transform:rotate(2deg) scale(1.04)}66%{transform:rotate(-1deg) scale(.98)}100%{transform:rotate(3deg) scale(1.02)}}
@keyframes tick{from{background:transparent}to{background:var(--accent);border-color:var(--accent)}}
.bt{position:absolute;left:0;right:0;top:4%;display:flex;flex-direction:column;align-items:center;gap:calc(var(--u)*.6)}
.bt-kick{font-weight:600;font-size:calc(var(--u)*2.4);letter-spacing:.32em;text-transform:uppercase;text-shadow:0 0 calc(var(--u)*1.5) rgba(0,0,0,.35)}
/* A magazine name: a high-contrast serif in white, set tight and stretched, with no box. */
.bt-word{font-family:"Playfair Display","Libre Baskerville",serif;font-weight:400;font-size:calc(var(--u)*var(--fs));line-height:.86;letter-spacing:-.035em;text-transform:uppercase;white-space:nowrap;color:var(--text);text-shadow:0 calc(var(--u)*.2) calc(var(--u)*1.6) rgba(0,0,0,.18)}
.bt-word span{display:inline-block;transform:scaleY(1.45);transform-origin:50% 0}
.look-bold .bt-word,.look-neon .bt-word{font-weight:400}

/* graph rising behind the speaker */
.gr{position:absolute;left:var(--side);right:var(--side);top:4%;height:34%}
.gr-labels{position:absolute;left:0;right:0;top:0;display:flex;justify-content:space-between;font-weight:600;font-size:calc(var(--u)*2.3);letter-spacing:.12em;color:rgba(255,255,255,.88);text-shadow:0 0 calc(var(--u)*1.2) rgba(0,0,0,.4)}
.gr-plot{position:absolute;left:0;right:0;top:12%;bottom:0}
.gr-line{position:absolute;inset:0;width:100%;height:100%;object-fit:fill;filter:drop-shadow(0 0 calc(var(--u)*.8) var(--accent))}
.gr-tag{position:absolute;right:-1%;top:-6%;font-weight:900;font-size:calc(var(--u)*3.4);line-height:1;color:var(--ink);background:var(--accent);padding:calc(var(--u)*.7) calc(var(--u)*1.5);border-radius:calc(var(--u)*5);transform-origin:100% 50%;box-shadow:0 0 calc(var(--u)*2) var(--accent)}
.a-wipe2{animation:reveal 1.1s cubic-bezier(.55,0,.25,1) calc(var(--t0) + .1s) both,fade .3s ease-in calc(var(--t0) + var(--d) - .3s) forwards}
.a-pop2{animation:pop .4s cubic-bezier(.2,1.6,.4,1) calc(var(--t0) + 1.1s) both,fade .3s ease-in calc(var(--t0) + var(--d) - .3s) forwards}

/* hand-drawn doodle */
.dd{position:absolute;width:calc(var(--u)*22);height:calc(var(--u)*22);margin:calc(var(--u)*-11) 0 0 calc(var(--u)*-11)}
.dd-boil{width:100%;height:100%;animation:boil .36s steps(1) calc(var(--t0)) infinite}
.dd img{width:100%;height:100%;filter:drop-shadow(0 0 calc(var(--u)*.4) rgba(0,0,0,.35))}

/* tick list: a dark glass card */
.ck{position:absolute;left:8%;right:8%;top:46%;background:rgba(24,25,28,.86);border:1px solid rgba(255,255,255,.08);border-radius:calc(var(--u)*2.4);padding:calc(var(--u)*2.2) calc(var(--u)*2.6) calc(var(--u)*2.6);display:flex;flex-direction:column;gap:calc(var(--u)*2.1);box-shadow:0 calc(var(--u)*1.5) calc(var(--u)*5) rgba(0,0,0,.35)}
.ck-head{display:flex;justify-content:space-between;align-items:center;font-weight:600;font-size:calc(var(--u)*2);letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,.55)}
.ck-count{position:relative;display:grid;letter-spacing:.04em;text-transform:none}
.ck-n{grid-area:1/1;justify-self:end;opacity:0;animation:win var(--len) step-end calc(var(--t0) + var(--on)) forwards}
@keyframes win{0%{opacity:1}100%{opacity:0}}
@keyframes show{from{opacity:0}to{opacity:1}}
.ck-bar{display:flex;height:calc(var(--u)*.45);margin-top:calc(var(--u)*-1);background:rgba(255,255,255,.1);border-radius:9px;overflow:hidden}
.ck-seg{flex:1;background:var(--accent);transform-origin:0 50%;animation:grow .25s ease-out calc(var(--t0) + var(--on)) both}
.ck-row{display:flex;align-items:center;gap:calc(var(--u)*2)}
.ck-box{flex:none;width:calc(var(--u)*4.2);height:calc(var(--u)*4.2);border-radius:50%;border:calc(var(--u)*.35) solid rgba(255,255,255,.35);display:flex;align-items:center;justify-content:center;animation:tick .18s ease-out calc(var(--t0) + var(--at) + var(--type)) both}
.ck-mark{width:72%;height:72%;animation:scribble .22s ease-out calc(var(--t0) + var(--at) + var(--type)) both}
.ck-slot{position:relative;flex:1;min-width:0;height:calc(var(--u)*4.4);display:flex;align-items:center}
.ck-skel{position:absolute;left:0;width:62%;height:calc(var(--u)*1.4);border-radius:9px;background:rgba(255,255,255,.12);animation:show .12s linear calc(var(--t0) + var(--at)) reverse both}
.ck-text{font-weight:600;font-size:calc(var(--u)*3.6);line-height:1.1;white-space:nowrap;overflow:hidden;clip-path:inset(0 100% 0 0);animation:type var(--type) steps(var(--chars)) calc(var(--t0) + var(--at)) both}
@keyframes type{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}

/* logo */
.lg{position:absolute;right:var(--side);top:5%;width:calc(var(--u)*14);height:calc(var(--u)*14);display:flex;align-items:center;justify-content:center;transform-origin:100% 0}
.lg img{max-width:100%;max-height:100%;object-fit:contain;filter:drop-shadow(0 calc(var(--u)*.3) calc(var(--u)*1.5) rgba(0,0,0,.4))}
"""
