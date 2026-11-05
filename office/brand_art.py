"""Compose a feed-ready image from the agent's brief and the company's logo."""
from io import BytesIO
import re
import unicodedata

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps


SIZE = 1080
BACKGROUND = "#0B1020"
FOREGROUND = "#F8FAFC"


def _brief(body):
    fields = {}
    for line in body.splitlines():
        label, separator, value = line.replace("**", "").lstrip("# -•\t").partition(":")
        if not separator:
            continue
        label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().strip().lower()
        if label in ("titulo da arte", "subtitulo da arte", "cta da arte"):
            fields.setdefault(label, " ".join(value.strip(" \"“”").split()))
    return fields


def _font(size, bold=False):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _wrap(draw, text, font, width):
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _text_block(draw, text, *, x, y, width, sizes, max_lines, color, bold=False):
    for size in sizes:
        font = _font(size, bold)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines and all(draw.textlength(line, font=font) <= width for line in lines):
            break
    else:
        lines = lines[:max_lines]
        if lines:
            while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
                lines[-1] = lines[-1][:-1]
            lines[-1] = lines[-1].rstrip() + "…"
    for index, line in enumerate(lines):
        while line and draw.textlength(line, font=font) > width:
            line = line[:-1]
        draw.text((x, y + round(size * 1.16) * index), line, font=font, fill=color, anchor="lt")
    return y + round(size * 1.16) * len(lines)


def _logo(canvas, workspace):
    if not workspace.logo:
        return False
    try:
        with workspace.logo.open("rb") as source, Image.open(source) as original:
            if original.width * original.height > 24_000_000:
                return False
            mark = original.convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError):
        return False
    bounds = mark.getbbox()
    if not bounds:
        return False
    mark = ImageOps.contain(mark.crop(bounds), (230, 90), Image.Resampling.LANCZOS)
    canvas.paste(mark, (80, 76 + (90 - mark.height) // 2), mark)
    return True


def render(delivery):
    workspace = delivery.workspace
    fields = _brief(delivery.body)
    title = (fields.get("titulo da arte") or delivery.title)[:110]
    subtitle = fields.get("subtitulo da arte", "")[:165]
    cta = fields.get("cta da arte", "")[:45]
    try:
        accent = ImageColor.getrgb(workspace.brand_color)
    except ValueError:
        accent = ImageColor.getrgb("#8C6BFF")
    canvas = Image.new("RGB", (SIZE, SIZE), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    light_accent = tuple(round(channel * .7 + 255 * .3) for channel in accent)
    dim_accent = tuple(round(channel * .22 + base * .78) for channel, base in zip(accent, ImageColor.getrgb(BACKGROUND)))
    draw.ellipse((666, 552, 1230, 1120), outline=dim_accent, width=3)
    draw.ellipse((722, 604, 1174, 1067), outline=dim_accent, width=2)
    draw.line((81, 192, 999, 192), fill="#253149", width=2)
    draw.rounded_rectangle((80, 218, 154, 229), radius=6, fill=accent)
    if not _logo(canvas, workspace):
        _text_block(draw, workspace.name[:54], x=80, y=98, width=720, sizes=(46, 40, 34), max_lines=1,
                    color=FOREGROUND, bold=True)
    next_y = _text_block(draw, title, x=80, y=284, width=920, sizes=(114, 100, 88, 76, 64),
                         max_lines=3, color=FOREGROUND, bold=True)
    if subtitle:
        _text_block(draw, subtitle, x=82, y=next_y + 37, width=850, sizes=(48, 43, 38, 34), max_lines=3,
                    color="#CFD9EA")
    if cta:
        draw.rounded_rectangle((80, 888, 854, 977), radius=18, fill="#17243D", outline=dim_accent, width=2)
        _text_block(draw, cta, x=113, y=917, width=710, sizes=(37, 33, 30, 26), max_lines=1,
                    color=light_accent, bold=True)
    draw.ellipse((944, 898, 977, 931), fill=accent)
    buffer = BytesIO()
    canvas.save(buffer, "JPEG", quality=92, optimize=True, progressive=True)
    return buffer.getvalue()
