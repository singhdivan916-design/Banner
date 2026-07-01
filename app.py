"""
Free Fire Profile Banner Generator API
========================================

GET /banner?uid=<uid>            -> PNG image (inline)
GET /banner?uid=<uid>&dl=1        -> PNG image (as attachment / forced download)
GET /health                       -> simple health check

How it works
------------
1. Calls the player-info API to get the account data for the given uid.
2. Pulls out only the fields the card actually needs (nickname, guild,
   level, bannerId, headPic, badgeId, primeLevel).
3. Downloads the matching item art (banner background / avatar / badge)
   from the jsDelivr icon CDN.
4. Composites everything with Pillow into a 2048x512 card that matches
   the reference layout (avatar box on the left, banner + name/guild/level
   text on the right) and streams it back as a PNG.

Run:
    pip install -r requirements.txt
    python app.py
    -> http://localhost:5000/banner?uid=14709492693
"""

import io
import time
import logging
import threading
from typing import Optional, Tuple

import requests
from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

PLAYER_INFO_API = "https://info.killersharmabot.online/player-info?uid={uid}"
ICON_CDN = "https://cdn.jsdelivr.net/gh/ShahGCreator/icon@main/PNG/{item_id}.png"

HTTP_TIMEOUT = 10
PLAYER_CACHE_TTL = 60 * 5      # 5 minutes - player stats change
IMAGE_CACHE_TTL = 60 * 60 * 24  # 24 hours - cosmetic art almost never changes

CANVAS_W, CANVAS_H = 2048, 512
AVATAR_BOX = 512          # left square panel (== canvas height)
BORDER = 14                # white border thickness around the avatar art
BADGE_SIZE = 118           # bottom-left hex badge
CROWN_W, CROWN_H = 150, 108  # top-right prime-level crown

FONT_PATH = "fonts/Poppins-Bold.ttf"
# FF nicknames are often full of decorative Unicode (circled letters,
# Cherokee/Coptic look-alikes, etc.) that Poppins doesn't contain glyphs
# for. FreeSans has much broader coverage, so it's used as a fallback for
# any character Poppins can't render, character-by-character.
FALLBACK_FONT_PATH = "fonts/FreeSansBold.ttf"

app = Flask(__name__)
log = logging.getLogger("ff_banner")
logging.basicConfig(level=logging.INFO)

# --------------------------------------------------------------------------
# Tiny in-memory TTL caches (swap for redis/memcached if you scale this out)
# --------------------------------------------------------------------------

_lock = threading.Lock()
_player_cache = {}   # uid -> (expires_at, json)
_image_cache = {}    # item_id -> (expires_at, PIL.Image in RGBA)


def _cache_get(cache: dict, key):
    with _lock:
        entry = cache.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            cache.pop(key, None)
            return None
        return value


def _cache_set(cache: dict, key, value, ttl):
    with _lock:
        cache[key] = (time.time() + ttl, value)


# --------------------------------------------------------------------------
# Data fetching
# --------------------------------------------------------------------------

class PlayerNotFound(Exception):
    pass


class UpstreamError(Exception):
    pass


def fetch_player_info(uid: str) -> dict:
    cached = _cache_get(_player_cache, uid)
    if cached is not None:
        return cached

    url = PLAYER_INFO_API.format(uid=uid)
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
    except requests.RequestException as exc:
        raise UpstreamError(f"player-info request failed: {exc}") from exc

    if resp.status_code == 404:
        raise PlayerNotFound(uid)
    if resp.status_code != 200:
        raise UpstreamError(f"player-info returned HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise UpstreamError("player-info returned invalid JSON") from exc

    if not data or "basicInfo" not in data:
        raise PlayerNotFound(uid)

    _cache_set(_player_cache, uid, data, PLAYER_CACHE_TTL)
    return data


def fetch_item_image(item_id) -> Optional[Image.Image]:
    """Download a cosmetic PNG from the icon CDN. Returns None on failure
    so the caller can fall back to a placeholder instead of crashing the
    whole banner."""
    if not item_id:
        return None

    cached = _cache_get(_image_cache, item_id)
    if cached is not None:
        return cached

    url = ICON_CDN.format(item_id=item_id)
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - any failure just means "no art"
        log.warning("Could not fetch icon %s: %s", item_id, exc)
        return None

    _cache_set(_image_cache, item_id, img, IMAGE_CACHE_TTL)
    return img


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------

def cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize + center-crop `img` so it fully covers a target_w x target_h
    box (like CSS `background-size: cover`)."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def placeholder(w: int, h: int, label: str, color=(60, 60, 66)) -> Image.Image:
    """Neutral placeholder used whenever an icon can't be downloaded."""
    img = Image.new("RGBA", (w, h), (*color, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w - 1, h - 1], outline=(120, 120, 130, 255), width=2)
    try:
        font = ImageFont.truetype(FONT_PATH, max(10, h // 8))
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((w - tw) / 2, (h - th) / 2), label, font=font,
                   fill=(220, 220, 225, 255))
    except Exception:
        pass
    return img


def draw_text_with_shadow(draw: ImageDraw.ImageDraw, xy, text, font,
                           fill=(255, 255, 255, 255),
                           shadow=(0, 0, 0, 160), offset=(0, 4)):
    x, y = xy
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


_font_cache = {}
_cmap_cache = {}


def _get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    font = _font_cache.get(key)
    if font is None:
        font = ImageFont.truetype(path, size)
        _font_cache[key] = font
    return font


def _get_cmap(path: str) -> set:
    cmap = _cmap_cache.get(path)
    if cmap is None:
        try:
            from fontTools.ttLib import TTFont
            tt = TTFont(path, fontNumber=0, lazy=True)
            cmap = set(tt.getBestCmap().keys())
        except Exception:
            cmap = set()  # if fontTools isn't available, just skip fallback
        _cmap_cache[path] = cmap
    return cmap


def _font_for_char(ch: str, size: int) -> ImageFont.FreeTypeFont:
    """Pick Poppins if it has the glyph, else fall back to FreeSans."""
    if ch == " " or ord(ch) in _get_cmap(FONT_PATH):
        return _get_font(FONT_PATH, size)
    if ord(ch) in _get_cmap(FALLBACK_FONT_PATH):
        return _get_font(FALLBACK_FONT_PATH, size)
    return _get_font(FONT_PATH, size)  # neither has it -> tofu box, rare


def measure_mixed_text(draw: ImageDraw.ImageDraw, text: str, size: int) -> int:
    return sum(draw.textlength(ch, font=_font_for_char(ch, size)) for ch in text)


def draw_mixed_text_with_shadow(draw: ImageDraw.ImageDraw, xy, text: str, size: int,
                                 fill=(255, 255, 255, 255),
                                 shadow=(0, 0, 0, 160), offset=(0, 4)):
    """Draws `text` left-to-right, choosing a glyph-covering font per
    character so unusual Unicode nicknames don't render as boxes."""
    x, y = xy
    for ch in text:
        font = _font_for_char(ch, size)
        draw.text((x + offset[0], y + offset[1]), ch, font=font, fill=shadow)
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font)
    return x


def draw_crown_badge(canvas: Image.Image, top_right_xy: Tuple[int, int],
                      number: int):
    """Draws the small gold 'prime level' crown badge (top-right corner of
    the avatar box) since the player-info API only gives us the number,
    not an icon id for it."""
    w, h = CROWN_W, CROWN_H
    badge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(badge)

    gold = (255, 196, 64, 255)
    gold_dark = (196, 130, 20, 255)

    # crown silhouette: three peaks
    pts = [
        (w * 0.06, h * 0.95), (w * 0.06, h * 0.42), (w * 0.24, h * 0.60),
        (w * 0.5, h * 0.05), (w * 0.76, h * 0.60), (w * 0.94, h * 0.42),
        (w * 0.94, h * 0.95),
    ]
    d.polygon(pts, fill=gold, outline=gold_dark)
    d.rectangle([w * 0.06, h * 0.85, w * 0.94, h * 0.98], fill=gold_dark)

    try:
        font = ImageFont.truetype(FONT_PATH, int(h * 0.5))
        text = str(number)
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((w - tw) / 2, h * 0.42 - th / 2), text, font=font,
               fill=(80, 40, 0, 255))
    except Exception:
        pass

    canvas.alpha_composite(badge, dest=top_right_xy)


# --------------------------------------------------------------------------
# Core compositing
# --------------------------------------------------------------------------

def build_banner(uid: str) -> Image.Image:
    data = fetch_player_info(uid)

    basic = data.get("basicInfo", {}) or {}
    clan = data.get("clanBasicInfo", {}) or {}

    nickname = basic.get("nickname") or "Unknown"
    level = basic.get("level", "?")
    banner_id = basic.get("bannerId")
    head_pic_id = basic.get("headPic")
    badge_id = basic.get("badgeId")
    prime_level = (basic.get("primeLevel") or {}).get("level")
    guild_name = clan.get("clanName") or "Solo Player"

    banner_art = fetch_item_image(banner_id)
    avatar_art = fetch_item_image(head_pic_id)
    badge_art = fetch_item_image(badge_id)

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (10, 10, 10, 255))

    # ---- right side: banner background art -------------------------------
    right_w = CANVAS_W - AVATAR_BOX
    if banner_art:
        bg = cover_resize(banner_art, right_w, CANVAS_H)
    else:
        bg = placeholder(right_w, CANVAS_H, "NO BANNER", color=(30, 30, 34))
    canvas.alpha_composite(bg, dest=(AVATAR_BOX, 0))

    # subtle left->right dark gradient behind the text for readability
    grad = Image.new("L", (right_w, CANVAS_H), 0)
    gdraw = ImageDraw.Draw(grad)
    fade_w = int(right_w * 0.55)
    for x in range(fade_w):
        alpha = int(150 * (1 - x / fade_w))
        gdraw.line([(x, 0), (x, CANVAS_H)], fill=alpha)
    shade = Image.new("RGBA", (right_w, CANVAS_H), (0, 0, 0, 255))
    shade.putalpha(grad)
    canvas.alpha_composite(shade, dest=(AVATAR_BOX, 0))

    # ---- left side: white panel + avatar art ------------------------------
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, AVATAR_BOX - 1, CANVAS_H - 1], fill=(255, 255, 255, 255))

    inner = AVATAR_BOX - 2 * BORDER
    if avatar_art:
        av = cover_resize(avatar_art, inner, inner)
    else:
        av = placeholder(inner, inner, "NO AVATAR", color=(70, 130, 180))
    canvas.alpha_composite(av, dest=(BORDER, BORDER))

    # crown / prime-level badge, top-right corner of the avatar box
    if prime_level is not None:
        draw_crown_badge(canvas, (AVATAR_BOX - CROWN_W - 6, 6), prime_level)

    # rank/season badge, bottom-left corner of the avatar box
    if badge_art:
        b = badge_art.resize((BADGE_SIZE, BADGE_SIZE), Image.LANCZOS)
    else:
        b = placeholder(BADGE_SIZE, BADGE_SIZE, "BADGE", color=(40, 40, 46))
    canvas.alpha_composite(b, dest=(8, AVATAR_BOX - BADGE_SIZE - 8))

    # ---- text: name / guild / level ---------------------------------------
    draw = ImageDraw.Draw(canvas)
    name_size = 84
    guild_size = 84
    level_size = 78

    text_x = AVATAR_BOX + 58
    draw_mixed_text_with_shadow(draw, (text_x, 68), nickname, name_size)
    draw_mixed_text_with_shadow(draw, (text_x, 330), guild_name, guild_size)

    level_text = f"Lvl. {level}"
    lw = measure_mixed_text(draw, level_text, level_size)
    draw_mixed_text_with_shadow(draw, (CANVAS_W - lw - 50, CANVAS_H - 120),
                                 level_text, level_size)

    return canvas.convert("RGB")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/banner")
def banner_route():
    uid = request.args.get("uid", "").strip()
    if not uid:
        return jsonify(error="Missing required query param 'uid'"), 400

    try:
        img = build_banner(uid)
    except PlayerNotFound:
        return jsonify(error=f"No player found for uid '{uid}'"), 404
    except UpstreamError as exc:
        return jsonify(error=str(exc)), 502
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected error building banner for uid=%s", uid)
        return jsonify(error="Internal error generating banner"), 500

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    as_attachment = request.args.get("dl") == "1"
    return send_file(buf, mimetype="image/png", as_attachment=as_attachment,
                      download_name=f"{uid}_banner.png" if as_attachment else None,
                      max_age=300)


@app.route("/health")
def health():
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
