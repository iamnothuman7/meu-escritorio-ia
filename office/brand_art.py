"""Compose a feed-ready image from the agent's brief and the company's logo."""
from io import BytesIO
from pathlib import Path
import re
import unicodedata

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps


SIZE = 1080
BACKGROUND = "#0B1020"
FOREGROUND = "#F8FAFC"
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
INTER_FONT = Path(__file__).with_name("static") / "office" / "fonts" / "Inter-latin.woff2"


def _hex_color(value):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return ImageColor.getrgb(value)
    return None


def _mix(color, base, amount):
    return tuple(round(channel * amount + neutral * (1 - amount)) for channel, neutral in zip(color, base))


def _luminance(color):
    channels = [channel / 255 for channel in color]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def _readable_ink(background, deep):
    lightness = _luminance(background) + .05
    if lightness / (_luminance(deep) + .05) >= 4.5:
        return deep
    return WHITE if 1.05 / lightness >= 4.5 else BLACK


def _palette(workspace):
    policy = getattr(workspace, "policy", None)
    art = policy.get("brand_art") if isinstance(policy, dict) else None
    background = _hex_color(art.get("background")) if isinstance(art, dict) else None
    if background is not None:
        details = _hex_color(art.get("details")) or WHITE
        deep = _mix(background, BLACK, .16)
        ink = _readable_ink(background, deep)
        return dict(
            background=background, headline=ink, subtitle=ink, details=details,
            outline=_mix(details, background, .28), divider=details,
            cta_background=deep, cta_text=WHITE,
        )

    try:
        accent = ImageColor.getrgb(workspace.brand_color)
    except ValueError:
        accent = ImageColor.getrgb("#8C6BFF")
    return dict(
        background=BACKGROUND, headline=FOREGROUND, subtitle="#CFD9EA", details=accent,
        outline=_mix(accent, ImageColor.getrgb(BACKGROUND), .22), divider="#253149",
        cta_background="#17243D", cta_text=tuple(round(channel * .7 + 255 * .3) for channel in accent),
    )


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
        pass
    try:
        font = ImageFont.truetype(INTER_FONT, size)
        if bold:
            font.set_variation_by_name("Bold")
        return font
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
    colors = _palette(workspace)
    canvas = Image.new("RGB", (SIZE, SIZE), colors["background"])
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((666, 552, 1230, 1120), outline=colors["outline"], width=3)
    draw.ellipse((722, 604, 1174, 1067), outline=colors["outline"], width=2)
    draw.line((81, 192, 999, 192), fill=colors["divider"], width=2)
    draw.rounded_rectangle((80, 218, 154, 229), radius=6, fill=colors["details"])
    if not _logo(canvas, workspace):
        _text_block(draw, workspace.name[:54], x=80, y=98, width=720, sizes=(46, 40, 34), max_lines=1,
                    color=colors["headline"], bold=True)
    next_y = _text_block(draw, title, x=80, y=284, width=920, sizes=(114, 100, 88, 76, 64),
                         max_lines=3, color=colors["headline"], bold=True)
    if subtitle:
        _text_block(draw, subtitle, x=82, y=next_y + 37, width=850, sizes=(48, 43, 38, 34), max_lines=3,
                    color=colors["subtitle"])
    if cta:
        draw.rounded_rectangle((80, 888, 854, 977), radius=18, fill=colors["cta_background"],
                               outline=colors["outline"], width=2)
        _text_block(draw, cta, x=113, y=917, width=710, sizes=(37, 33, 30, 26), max_lines=1,
                    color=colors["cta_text"], bold=True)
    draw.ellipse((944, 898, 977, 931), fill=colors["details"])
    buffer = BytesIO()
    canvas.save(buffer, "JPEG", quality=92, optimize=True, progressive=True)
    return buffer.getvalue()
