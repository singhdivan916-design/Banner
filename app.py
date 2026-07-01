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

# ... (lifespan, app, middleware, client, process_pool remain the same) ...

def load_unicode_font(size, font_file=FONT_FILE):
    # ... unchanged ...

async def fetch_image_bytes(item_id):
    """Try multiple possible URL patterns to fetch an item image."""
    if not item_id or str(item_id) in ("0", "None"):
        return None

    item_id = str(item_id).strip()
    base_url = "https://raw.githubusercontent.com/danger738/danger-item-library/main/PNG"

    # List of folder candidates to try
    folders_to_try = []

    # 1. First two digits of the ID (most common)
    if len(item_id) >= 2:
        folders_to_try.append(item_id[:2])

    # 2. Last two digits (some repos use that)
    if len(item_id) >= 2:
        folders_to_try.append(item_id[-2:])

    # 3. Batch folders 01-36 (original fallback)
    folders_to_try.extend([f"{i:02d}" for i in range(1, 37)])

    # Remove duplicates while preserving order
    seen = set()
    unique_folders = []
    for f in folders_to_try:
        if f not in seen:
            seen.add(f)
            unique_folders.append(f)

    for folder in unique_folders:
        url = f"{base_url}/{folder}/{item_id}.png"
        try:
            logger.info(f"Attempting to fetch: {url}")
            resp = await client.head(url)
            if resp.status_code == 200:
                img_resp = await client.get(url)
                logger.info(f"Successfully fetched {url}")
                return img_resp.content
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            continue

    logger.warning(f"No image found for item ID {item_id}")
    return None

# ... bytes_to_image, process_banner_image remain the same ...

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

        # Check for API errors
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

        # IDs from the new API
        avatar_id = profile_info.get("avatarId")          # e.g. 102000007
        banner_id = basic_info.get("bannerId")            # e.g. 901000022
        badge_id = basic_info.get("badgeId")              # e.g. 1001000098

        # Fetch images in parallel
        avatar_task = fetch_image_bytes(avatar_id)
        banner_task = fetch_image_bytes(banner_id)
        # Badge may not be in the same repo; try anyway, but it's okay if it fails
        badge_task = fetch_image_bytes(badge_id) if badge_id else asyncio.sleep(0)

        results = await asyncio.gather(avatar_task, banner_task, badge_task)
        avatar_bytes, banner_bytes, badge_bytes = results[0], results[1], results[2]

        if badge_bytes is None:
            badge_bytes = b''  # will become a transparent placeholder

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

# ... main block unchanged ...
