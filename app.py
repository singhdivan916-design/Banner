import io
import os
import asyncio
import httpx
import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from scipy import ndimage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await client.aclose()
    process_pool.shutdown()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INFO_API_URL = "http://187.127.175.208:5000/Bmw"
FONT_FILE = "arial_unicode_bold.otf"
FONT_CHEROKEE = "NotoSansCherokee.ttf"

client = httpx.AsyncClient(
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=10.0,
    follow_redirects=True
)

process_pool = ThreadPoolExecutor(max_workers=4)

def load_unicode_font(size, font_file=FONT_FILE):
    try:
        font_path = os.path.join(os.path.dirname(__file__), font_file)
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
        return ImageFont.load_default()
    except:
        return ImageFont.load_default()

async def fetch_image_bytes(item_id):
    if not item_id or str(item_id) in ("0", "None"):
        return None
    item_id = str(item_id).strip()
    url = f"https://cdn.jsdelivr.net/gh/ShahGCreator/icon@main/PNG/{item_id}.png"
    try:
        logger.info(f"Trying: {url}")
        resp = await client.head(url)
        if resp.status_code == 200:
            img_resp = await client.get(url)
            logger.info(f"✅ Fetched: {url}")
            return img_resp.content
    except Exception as e:
        logger.warning(f"Failed: {url} – {e}")
        return None
    logger.warning(f"❌ No image found for {item_id}")
    return None

def bytes_to_image(img_bytes):
    if img_bytes:
        try:
            return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        except Exception as e:
            logger.warning(f"Failed to convert bytes to image: {e}")
            return Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    return Image.new('RGBA', (100, 100), (0, 0, 0, 0))

def is_cherokee(char):
    code = ord(char)
    return (0x13A0 <= code <= 0x13FF) or (0xAB70 <= code <= 0xABBF)

def measure_mixed_text(draw, text, size):
    """Measure total width of a string that may contain Cherokee characters."""
    font_main = load_unicode_font(size)
    font_cherokee = load_unicode_font(size, FONT_CHEROKEE)
    total_width = 0
    for char in text:
        font = font_cherokee if is_cherokee(char) else font_main
        total_width += font.getlength(char)
    return total_width

def draw_mixed_text_with_shadow(draw, xy, text, size, shadow_offset=(3, 3), shadow_color='black', fill='white'):
    """Draw text with a drop shadow."""
    x, y = xy
    font_main = load_unicode_font(size)
    font_cherokee = load_unicode_font(size, FONT_CHEROKEE)
    # Draw shadow
    sx, sy = shadow_offset
    current_x = x + sx
    for char in text:
        font = font_cherokee if is_cherokee(char) else font_main
        draw.text((current_x, y + sy), char, font=font, fill=shadow_color)
        current_x += font.getlength(char)
    # Draw main text
    current_x = x
    for char in text:
        font = font_cherokee if is_cherokee(char) else font_main
        draw.text((current_x, y), char, font=font, fill=fill)
        current_x += font.getlength(char)

def resize_cover(img, target_w, target_h):
    """
    Scale `img` so it fully covers a target_w x target_h box (no letterboxing,
    no distortion), then center-crop the overflow. This replaces the old
    rotate()+fixed-percentage-crop+stretch-resize hack, which assumed a fixed
    source aspect ratio and produced misaligned / stretched banners whenever
    the fetched asset didn't match that assumption.
    """
    if target_w <= 0 or target_h <= 0:
        return Image.new("RGBA", (max(1, target_w), max(1, target_h)), (50, 50, 50, 255))

    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        return Image.new("RGBA", (target_w, target_h), (50, 50, 50, 255))

    scale = max(target_w / src_w, target_h / src_h)
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))
    return img

def crop_to_content(img, var_thresh=8.0, max_trim_frac=0.35, extra_zoom=0.0,
                     min_dim_frac=0.18):
    """
    Background-agnostic content crop. Works regardless of what color the
    unwanted padding/frame is (black, purple, white, gradient, etc.) because
    it measures relative difference from the image's own border, not an
    absolute brightness value, and it adapts across both "high contrast
    card vs. background" and "low contrast card vs. background" source
    assets (the two banner styles the API returns):

    1. Sample the actual background color from the image's outer border ring.
    2. Try a range of thresholds (relative to this image's own contrast) and
       take the largest connected component at each. Reject any candidate
       that's a sliver in either dimension (a decorative bar, not the real
       subject) or that covers almost the whole image (background wasn't
       separated). Among the valid candidates, keep tightening the threshold
       as long as the component stays reasonably solid/rectangular (high
       fill-ratio) — this is what gives a tight zoom without accidentally
       locking onto a thin strip like a gold bar.
    4. Trim inward from each edge of that bbox while the edge row/col is
       near-uniform (low variance) — strips flat decorative bars/borders
       that survived step 3.
    5. Optionally zoom in an extra bit further (extra_zoom), to bite off the
       soft blurred edge transition around the card.
    """
    try:
        rgb = img.convert("RGB")
        arr = np.array(rgb).astype(float)
        H, W, _ = arr.shape
        if H < 8 or W < 8:
            return img

        ring = 3
        border_pixels = np.concatenate([
            arr[:ring, :, :].reshape(-1, 3),
            arr[-ring:, :, :].reshape(-1, 3),
            arr[:, :ring, :].reshape(-1, 3),
            arr[:, -ring:, :].reshape(-1, 3),
        ], axis=0)
        bg_color = np.median(border_pixels, axis=0)
        diff = np.sqrt(((arr - bg_color) ** 2).sum(axis=2))
        mean, std = diff.mean(), diff.std()

        candidates = []
        for k in [0.3, 0.6, 0.9, 1.2, 1.6, 2.0]:
            t = mean + k * std
            mask = diff > t
            labeled, n = ndimage.label(mask, structure=np.ones((3, 3)))
            if n == 0:
                continue
            sizes = ndimage.sum(mask, labeled, range(1, n + 1))
            idx = np.argmax(sizes)
            comp_size = sizes[idx]
            comp_mask = labeled == (idx + 1)
            ys, xs = np.where(comp_mask)
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            bh, bw = y1 - y0, x1 - x0
            coverage = comp_size / (H * W)
            fill_ratio = comp_size / max(1, bh * bw)
            valid = (bh >= min_dim_frac * H and bw >= min_dim_frac * W
                     and 0.02 < coverage < 0.93)
            candidates.append((k, valid, fill_ratio, y0, y1, x0, x1))

        valid_candidates = sorted([c for c in candidates if c[1]], key=lambda c: c[0])
        if not valid_candidates:
            return img

        chosen = valid_candidates[0]
        for c in valid_candidates:
            if c[2] >= 0.45:  # fill_ratio good enough -> keep tightening
                chosen = c

        _, _, _, y0, y1, x0, x1 = chosen

        pad = 2
        y0, x0 = max(0, y0 - pad), max(0, x0 - pad)
        y1, x1 = min(H, y1 + pad), min(W, x1 + pad)

        gray = arr.mean(axis=2)
        max_trim_y = int((y1 - y0) * max_trim_frac)
        max_trim_x = int((x1 - x0) * max_trim_frac)

        trimmed = 0
        while y1 - y0 > 4 and trimmed < max_trim_y:
            if gray[y0, x0:x1].std() < var_thresh:
                y0 += 1; trimmed += 1
            else:
                break
        trimmed = 0
        while y1 - y0 > 4 and trimmed < max_trim_y:
            if gray[y1 - 1, x0:x1].std() < var_thresh:
                y1 -= 1; trimmed += 1
            else:
                break
        trimmed = 0
        while x1 - x0 > 4 and trimmed < max_trim_x:
            if gray[y0:y1, x0].std() < var_thresh:
                x0 += 1; trimmed += 1
            else:
                break
        trimmed = 0
        while x1 - x0 > 4 and trimmed < max_trim_x:
            if gray[y0:y1, x1 - 1].std() < var_thresh:
                x1 -= 1; trimmed += 1
            else:
                break

        if extra_zoom > 0:
            zy = int((y1 - y0) * extra_zoom)
            zx = int((x1 - x0) * extra_zoom)
            y0, y1 = y0 + zy, y1 - zy
            x0, x1 = x0 + zx, x1 - zx

        if y1 - y0 < 4 or x1 - x0 < 4:
            return img

        return img.crop((x0, y0, x1, y1))
    except Exception as e:
        logger.warning(f"crop_to_content failed, using full image: {e}")
        return img

def process_banner_image(data, avatar_bytes, banner_bytes, pin_bytes):
    try:
        CANVAS_W, CANVAS_H = 2048, 512
        BORDER = 14
        # The avatar box (avatar + border on all sides) must exactly match the
        # canvas height, otherwise paste() silently clips it and the bottom
        # border disappears (this was the main cause of the misalignment).
        AVATAR_BOX = CANVAS_H
        AVATAR_SIZE = AVATAR_BOX - 2 * BORDER

        avatar_img = bytes_to_image(avatar_bytes)
        banner_img = bytes_to_image(banner_bytes)
        pin_img = bytes_to_image(pin_bytes)

        level = str(data.get("AccountLevel", "Not Found"))
        nickname = data.get("AccountName", "Not Found")
        guild_name = data.get("GuildName", "Not Found")

        # ----- Avatar: crop away any unwanted background/frame first (works
        # for any background color), then cover-fit into its box -----
        avatar_img = crop_to_content(avatar_img)
        avatar_img = resize_cover(avatar_img, AVATAR_SIZE, AVATAR_SIZE)
        bordered_avatar = Image.new("RGBA", (AVATAR_BOX, AVATAR_BOX), (255, 255, 255, 255))
        bordered_avatar.paste(avatar_img, (BORDER, BORDER), avatar_img)

        # ----- Banner: crop out the unwanted padding/background (any color)
        # so only the actual card artwork remains, then stretch that tight
        # crop left-to-right to fill the banner area edge-to-edge -----
        target_banner_w = CANVAS_W - AVATAR_BOX
        banner_img = crop_to_content(banner_img, extra_zoom=0.04)
        banner_img = banner_img.resize((target_banner_w, CANVAS_H), Image.LANCZOS)

        # ----- Combine -----
        combined = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        combined.paste(bordered_avatar, (0, 0), bordered_avatar)
        combined.paste(banner_img, (AVATAR_BOX, 0))

        draw = ImageDraw.Draw(combined)

        # ----- Text drawing (shadow style) -----
        name_size = 84
        guild_size = 84
        level_size = 78

        text_x = AVATAR_BOX + 58  # avatar box width + offset

        # Draw nickname
        draw_mixed_text_with_shadow(draw, (text_x, 68), nickname, name_size, shadow_offset=(3, 3))

        # Draw guild name
        draw_mixed_text_with_shadow(draw, (text_x, 330), guild_name, guild_size, shadow_offset=(3, 3))

        # Draw level (right‑bottom aligned)
        level_text = f"Lvl. {level}"
        lw = measure_mixed_text(draw, level_text, level_size)
        level_x = CANVAS_W - lw - 50
        level_y = CANVAS_H - 120
        draw_mixed_text_with_shadow(draw, (level_x, level_y), level_text, level_size, shadow_offset=(3, 3))

        # ----- Pin badge (lower left) -----
        if pin_img and pin_img.size != (100, 100):
            pin_size = 160
            pin_img = pin_img.resize((pin_size, pin_size), Image.LANCZOS)
            combined.paste(pin_img, (0, CANVAS_H - pin_size), pin_img)

        img_io = io.BytesIO()
        combined.save(img_io, 'PNG')
        img_io.seek(0)
        return img_io

    except Exception as e:
        logger.error(f"Error in process_banner_image: {traceback.format_exc()}")
        fallback = Image.new("RGB", (2048, 512), (50, 50, 50))
        draw = ImageDraw.Draw(fallback)
        try:
            font = load_unicode_font(80)
            draw.text((100, 200), f"Banner generation failed: {str(e)}", font=font, fill="white")
        except:
            pass
        img_io = io.BytesIO()
        fallback.save(img_io, 'PNG')
        img_io.seek(0)
        return img_io

@app.get("/")
async def home():
    return {
        "message": "⚡ Ultra Fast Banner API Running",
        "Fix By": "agajayofficial",
        "Telegram": "@agajayofficial",
        "Your Info Api": INFO_API_URL,
        "Api Endpoint": "/banner-image?uid={uid}",
        "Note": "Join To @AjayApis For More 💝"
    }

@app.get("/banner-image")
async def get_banner(uid: str):
    if not uid:
        raise HTTPException(status_code=400, detail="UID required")

    try:
        resp = await client.get(f"{INFO_API_URL}?uid={uid}")
        if resp.status_code != 200:
            logger.error(f"Info API returned {resp.status_code} for UID {uid}")
            raise HTTPException(status_code=502, detail="Info API Error")

        data = resp.json()
        logger.info(f"Response keys: {data.keys() if isinstance(data, dict) else 'non-dict'}")

        if isinstance(data, dict):
            if "error" in data:
                raise HTTPException(status_code=404, detail=f"Info API error: {data['error']}")
            if "message" in data and "not found" in data["message"].lower():
                raise HTTPException(status_code=404, detail=f"Info API error: {data['message']}")

        basic_info = data.get("basic_info")
        if not basic_info:
            error_msg = data.get("error") or data.get("message") or "User not found or invalid UID"
            raise HTTPException(status_code=404, detail=error_msg)

        clan_info = data.get("clan_basic_info", {})

        level = basic_info.get("level", "Not Found")
        nickname = basic_info.get("nickname", "Not Found")
        guild_name = clan_info.get("clan_name", "Not Found")

        avatar_id = basic_info.get("head_pic")
        banner_id = basic_info.get("banner_id")
        badge_id = basic_info.get("badge_id") or basic_info.get("title")

        avatar_task = fetch_image_bytes(avatar_id)
        banner_task = fetch_image_bytes(banner_id)
        badge_task = fetch_image_bytes(badge_id) if badge_id else asyncio.sleep(0)

        results = await asyncio.gather(avatar_task, banner_task, badge_task)
        avatar_bytes, banner_bytes, badge_bytes = results[0], results[1], results[2]
        if badge_bytes is None:
            badge_bytes = b''

        loop = asyncio.get_event_loop()
        banner_data = {
            "AccountLevel": level,
            "AccountName": nickname,
            "GuildName": guild_name
        }

        img_io = await loop.run_in_executor(
            process_pool,
            process_banner_image,
            banner_data, avatar_bytes, banner_bytes, badge_bytes
        )

        return Response(
            content=img_io.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=300"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error for UID {uid}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5000)
