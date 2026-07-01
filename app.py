import io
import os
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor

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
    if not item_id or str(item_id) == "0" or item_id is None:
        return None

    item_id = str(item_id)
    
    # Try all batch folders (01 to 36)
    for batch_num in range(1, 37):
        batch_str = f"{batch_num:02d}"
        url = f"https://raw.githubusercontent.com/danger738/danger-item-library/main/PNG/{batch_str}/{item_id}.png"
        
        try:
            resp = await client.head(url)
            if resp.status_code == 200:
                img_resp = await client.get(url)
                return img_resp.content
        except:
            continue
    return None

def bytes_to_image(img_bytes):
    if img_bytes:
        return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    return Image.new('RGBA', (100, 100), (0, 0, 0, 0))

def process_banner_image(data, avatar_bytes, banner_bytes, pin_bytes):
    avatar_img = bytes_to_image(avatar_bytes)
    banner_img = bytes_to_image(banner_bytes)
    pin_img = bytes_to_image(pin_bytes)

    level = str(data.get("AccountLevel", "Not Found"))
    name = data.get("AccountName", "Not Found")
    guild = data.get("GuildName", "Not Found")

    TARGET_HEIGHT = 400 
    avatar_img = avatar_img.resize((TARGET_HEIGHT, TARGET_HEIGHT), Image.LANCZOS)
    
    b_w, b_h = banner_img.size
    if b_w > 50 and b_h > 50:
        banner_img = banner_img.rotate(3, resample=Image.BICUBIC, expand=True)
        b_w, b_h = banner_img.size
        
        crop_top, crop_bottom, crop_sides = 0.23, 0.32, 0.17
        left, top = b_w * crop_sides, b_h * crop_top
        right, bottom = b_w * (1 - crop_sides), b_h * (1 - crop_bottom)
        banner_img = banner_img.crop((left, top, right, bottom))

    b_w, b_h = banner_img.size
    if b_h > 0:
        new_banner_w = int(TARGET_HEIGHT * (b_w / b_h) * 2.0)
        banner_img = banner_img.resize((new_banner_w, TARGET_HEIGHT), Image.LANCZOS)
    else:
        banner_img = Image.new("RGBA", (800, 400), (50, 50, 50))

    final_w = TARGET_HEIGHT + new_banner_w
    final_h = TARGET_HEIGHT
    combined = Image.new("RGBA", (final_w, final_h), (0, 0, 0, 0))
    combined.paste(avatar_img, (0, 0))
    combined.paste(banner_img, (TARGET_HEIGHT, 0))
    
    draw = ImageDraw.Draw(combined)
    
    font_large = load_unicode_font(125) 
    font_large_cherokee = load_unicode_font(125, FONT_CHEROKEE)
    font_small = load_unicode_font(95) 
    font_small_cherokee = load_unicode_font(95, FONT_CHEROKEE)
    font_level = load_unicode_font(50)

    text_x = TARGET_HEIGHT + 40 
    text_y = 40 
    
    def is_cherokee(char):
        code = ord(char)
        return (0x13A0 <= code <= 0x13FF) or (0xAB70 <= code <= 0xABBF)

    def draw_text_with_stroke(x, y, text, font_main, font_fallback, size):
        current_x = x
        for char in text:
            font = font_fallback if is_cherokee(char) else font_main
            
            # Draw stroke
            for dx in range(-size, size + 1):
                for dy in range(-size, size + 1):
                    draw.text((current_x + dx, y + dy), char, font=font, fill=stroke_col)
            
            # Draw text
            draw.text((current_x, y), char, font=font, fill=text_col)
            
            # Advance cursor
            char_width = font.getlength(char)
            current_x += char_width

    stroke_col, text_col = "black", "white"
    draw_text_with_stroke(text_x + 25, text_y, name, font_large, font_large_cherokee, 4)
    draw_text_with_stroke(text_x + 25, text_y + 200, guild, font_small, font_small_cherokee, 3)

    if pin_img and pin_img.size != (100, 100):
        pin_size = 130 
        pin_img = pin_img.resize((pin_size, pin_size), Image.LANCZOS)
        combined.paste(pin_img, (0, TARGET_HEIGHT - pin_size), pin_img)

    level_txt = f"Lvl.{level}"
    try:
        bbox = draw.textbbox((0, 0), level_txt, font=font_level)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except:
        text_w, text_h = len(level_txt) * 20, 40

    px, py = 25, 16
    box_x = final_w - (text_w + px * 2)
    box_y = final_h - (text_h + py * 2)
    
    draw.rectangle([box_x, box_y, final_w, final_h], fill="black")
    draw.text((box_x + px, box_y + py - 6), level_txt, font=font_level, fill="white")

    img_io = io.BytesIO()
    combined.save(img_io, 'PNG')
    img_io.seek(0)
    return img_io

@app.get("/")
async def home():
    return {"message": "⚡ Ultra Fast Banner API Running",
           "Telegram": "@FireXDecoder",
           "Api Endpoint": "/banner?uid={uid}",
           "Note": "Join To @FireXDecoding For More 💝"
    }

@app.get("/banner-image")
async def get_banner(uid: str):
    if not uid:
        raise HTTPException(status_code=400, detail="UID required")

    try:
        resp = await client.get(f"{INFO_API_URL}?uid={uid}")
        
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Info API Error")
            
        data = resp.json()
        
        # Extract data from new API structure
        basic_info = data.get("basicInfo", {})
        clan_info = data.get("clanBasicInfo", {})
        profile_info = data.get("profileInfo", {})
        
        if not basic_info:
            raise HTTPException(status_code=404, detail="Not Found")
        
        level = basic_info.get("level", "Not Found")
        name = basic_info.get("nickname", "Not Found")
        guild = clan_info.get("clanName") or clan_info.get("name") or "Not Found"
        
        avatar_id = profile_info.get("avatarId")
        banner_id = basic_info.get("bannerId")
        pin_id = basic_info.get("badgeId")  # Use badge as pin, if available
        
        avatar_task = fetch_image_bytes(avatar_id)
        banner_task = fetch_image_bytes(banner_id)
        pin_task = fetch_image_bytes(pin_id) if pin_id else asyncio.sleep(0)

        results = await asyncio.gather(avatar_task, banner_task, pin_task)
        avatar_bytes, banner_bytes, pin_bytes = results[0], results[1], results[2]
        
        if pin_bytes is None: pin_bytes = b''

        loop = asyncio.get_event_loop()
        banner_data = {
            "AccountLevel": level,
            "AccountName": name,
            "GuildName": guild
        }
        
        img_io = await loop.run_in_executor(
            process_pool, 
            process_banner_image, 
            banner_data, avatar_bytes, banner_bytes, pin_bytes
        )
        
        return Response(content=img_io.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=300"})

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5000)
