import io
import os
import asyncio
import httpx
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor

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

INFO_API_URL = "https://info.killersharmabot.online/player-info"
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
    """Fetch an image from the new CDN repository."""
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
        return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    return Image.new('RGBA', (100, 100), (0, 0, 0, 0))

def process_banner_image(data, avatar_bytes, banner_bytes, pin_bytes):
    # New canvas dimensions
    CANVAS_W, CANVAS_H = 2048, 512
    AVATAR_SIZE = 512
    BORDER = 14

    avatar_img = bytes_to_image(avatar_bytes)
    banner_img = bytes_to_image(banner_bytes)
    pin_img = bytes_to_image(pin_bytes)

    level = str(data.get("AccountLevel", "Not Found"))
    name = data.get("AccountName", "Not Found")
    guild = data.get("GuildName", "Not Found")

    # ----- Avatar (with border) -----
    avatar_img = avatar_img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)

    # Create a bordered avatar
    bordered_avatar = Image.new("RGBA", (AVATAR_SIZE + 2 * BORDER, AVATAR_SIZE + 2 * BORDER), (255, 255, 255, 255))
    bordered_avatar.paste(avatar_img, (BORDER, BORDER), avatar_img)

    # ----- Banner -----
    b_w, b_h = banner_img.size
    if b_w > 50 and b_h > 50:
        banner_img = banner_img.rotate(3, resample=Image.BICUBIC, expand=True)
        b_w, b_h = banner_img.size
        # Crop to remove empty edges after rotation
        crop_top, crop_bottom, crop_sides = 0.23, 0.32, 0.17
        left, top = b_w * crop_sides, b_h * crop_top
        right, bottom = b_w * (1 - crop_sides), b_h * (1 - crop_bottom)
        banner_img = banner_img.crop((left, top, right, bottom))

    # Resize banner to fill the remaining width, maintaining aspect ratio
    b_w, b_h = banner_img.size
    if b_h > 0:
        # We want the banner to be at least as wide as the remaining canvas
        target_banner_w = CANVAS_W - AVATAR_SIZE - 2 * BORDER  # exact remaining width
        # Scale so height = CANVAS_H, width proportional
        scale = CANVAS_H / b_h
        new_banner_w = int(b_w * scale)
        # If new_banner_w < target_banner_w, we need to zoom in (increase scale to cover width)
        if new_banner_w < target_banner_w:
            scale = target_banner_w / b_w
            new_banner_h = int(b_h * scale)
            new_banner_w = target_banner_w
            # But we must keep height = CANVAS_H? Actually we want to fill the canvas exactly.
            # Better: resize to height = CANVAS_H and then crop horizontally to target width.
            # Simpler: resize to height = CANVAS_H and width = new_banner_w, then crop center.
        # We'll resize to height = CANVAS_H, width calculated, then crop horizontally to target width.
        banner_img = banner_img.resize((new_banner_w, CANVAS_H), Image.LANCZOS)
        # Crop horizontally to target width
        b_w, b_h = banner_img.size
        if b_w > target_banner_w:
            left = (b_w - target_banner_w) // 2
            right = left + target_banner_w
            banner_img = banner_img.crop((left, 0, right, CANVAS_H))
        else:
            # if still smaller, we can pad or stretch; we'll stretch to fill
            banner_img = banner_img.resize((target_banner_w, CANVAS_H), Image.LANCZOS)
    else:
        banner_img = Image.new("RGBA", (CANVAS_W - AVATAR_SIZE - 2 * BORDER, CANVAS_H), (50, 50, 50))

    # ----- Combine -----
    combined = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    # Paste bordered avatar
    combined.paste(bordered_avatar, (0, 0), bordered_avatar)
    # Paste banner next to it
    combined.paste(banner_img, (AVATAR_SIZE + 2 * BORDER, 0))

    draw = ImageDraw.Draw(combined)

    # ----- Fonts (scaled for new canvas) -----
    font_large = load_unicode_font(160)          # was 125
    font_large_cherokee = load_unicode_font(160, FONT_CHEROKEE)
    font_small = load_unicode_font(122)          # was 95
    font_small_cherokee = load_unicode_font(122, FONT_CHEROKEE)
    font_level = load_unicode_font(64)           # was 50

    text_x = AVATAR_SIZE + 2 * BORDER + 60       # offset from banner start
    text_y = 50

    def is_cherokee(char):
        code = ord(char)
        return (0x13A0 <= code <= 0x13FF) or (0xAB70 <= code <= 0xABBF)

    def draw_text_with_stroke(x, y, text, font_main, font_fallback, size):
        current_x = x
        for char in text:
            font = font_fallback if is_cherokee(char) else font_main
            # stroke
            for dx in range(-size, size + 1):
                for dy in range(-size, size + 1):
                    draw.text((current_x + dx, y + dy), char, font=font, fill=stroke_col)
            # main text
            draw.text((current_x, y), char, font=font, fill=text_col)
            char_width = font.getlength(char)
            current_x += char_width

    stroke_col, text_col = "black", "white"
    draw_text_with_stroke(text_x, text_y, name, font_large, font_large_cherokee, 4)
    draw_text_with_stroke(text_x, text_y + 240, guild, font_small, font_small_cherokee, 3)  # y offset adjusted

    # ----- Pin (badge) -----
    if pin_img and pin_img.size != (100, 100):
        pin_size = 160  # scaled up
        pin_img = pin_img.resize((pin_size, pin_size), Image.LANCZOS)
        combined.paste(pin_img, (0, CANVAS_H - pin_size), pin_img)

    # ----- Level Box (bottom right) -----
    level_txt = f"Lvl.{level}"
    try:
        bbox = draw.textbbox((0, 0), level_txt, font=font_level)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except:
        text_w, text_h = len(level_txt) * 30, 60

    px, py = 35, 25
    box_x = CANVAS_W - (text_w + px * 2)
    box_y = CANVAS_H - (text_h + py * 2)
    draw.rectangle([box_x, box_y, CANVAS_W, CANVAS_H], fill="black")
    draw.text((box_x + px, box_y + py - 8), level_txt, font=font_level, fill="white")

    img_io = io.BytesIO()
    combined.save(img_io, 'PNG')
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

        basic_info = data.get("basicInfo")
        if not basic_info:
            error_msg = data.get("error") or data.get("message") or "User not found or invalid UID"
            raise HTTPException(status_code=404, detail=error_msg)

        clan_info = data.get("clanBasicInfo", {})
        profile_info = data.get("profileInfo", {})

        level = basic_info.get("level", "Not Found")
        name = basic_info.get("nickname", "Not Found")
        guild = clan_info.get("clanName") or clan_info.get("name") or "Not Found"

        avatar_id = profile_info.get("avatarId")
        banner_id = basic_info.get("bannerId")
        badge_id = basic_info.get("badgeId")

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
            "AccountName": name,
            "GuildName": guild
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
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5000)
