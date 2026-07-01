# Free Fire Profile Banner API

Generates a 2048x512 profile banner (avatar box + name/guild/level card,
matching the reference layout) for any Free Fire UID.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open:
```
http://localhost:5000/banner?uid=14709492693
```

Add `&dl=1` to force a download instead of inline display.

## How it works

1. `GET /banner?uid=<uid>` hits `https://info.killersharmabot.online/player-info?uid=<uid>`.
2. Pulls `nickname`, `level`, `bannerId`, `headPic`, `badgeId`, `primeLevel.level`
   from `basicInfo`, and `clanName` from `clanBasicInfo`.
3. Downloads the matching art from
   `https://cdn.jsdelivr.net/gh/ShahGCreator/icon@main/PNG/{id}.png` for the
   banner background, avatar bust, and badge icon.
4. Composites everything with Pillow:
   - Left 512x512 white-bordered panel: avatar art, gold "prime level"
     crown badge (drawn in code — the API only gives the number, not an
     icon id) top-right, and the badge icon bottom-left.
   - Right 1536x512: banner art (cropped to cover, like CSS
     `background-size: cover`) with a dark left-to-right gradient behind
     the text for legibility, plus nickname / guild name / "Lvl. N".
5. Streams the result back as `image/png`.

## Notes / things you may want to tune

- **Caching**: player stats are cached in-memory for 5 minutes, item art
  for 24 hours (`PLAYER_CACHE_TTL` / `IMAGE_CACHE_TTL` in `app.py`). This
  is per-process — swap the `_player_cache` / `_image_cache` dicts for
  Redis if you run more than one worker/replica, or the caches won't be
  shared.
- **Fonts**: `fonts/Poppins-Bold.ttf` is the primary font (closest visual
  match to the reference). `fonts/FreeSansBold.ttf` is a fallback used
  character-by-character for decorative Unicode some FF nicknames use
  (circled letters, Cherokee/Coptic look-alikes, etc.) that Poppins
  doesn't contain. A handful of very rare glyphs (e.g. Tibetan, Hangul)
  still won't render — bundling full CJK coverage would add ~100MB+, so
  that's a deliberate size/coverage trade-off. Swap in a bigger fallback
  font if you need broader coverage.
- **Missing data / failed downloads**: if the uid doesn't exist you get a
  `404` JSON error; if the player-info API errors out you get `502`; if a
  specific icon (avatar/banner/badge) fails to download, that slot falls
  back to a neutral placeholder box instead of failing the whole request.
- **Crown badge**: drawn procedurally in `draw_crown_badge()` since there's
  no icon id for it in the API response — only `primeLevel.level`. Restyle
  that function if you want a different look.
- Layout constants (`AVATAR_BOX`, `BORDER`, font sizes, text x/y) are at
  the top of `app.py` / inside `build_banner()` if you want to tweak
  spacing.

## Testing without hitting the real APIs

`test_local.py` monkey-patches `fetch_player_info` / `fetch_item_image`
with the sample JSON payload (`sample_player.json`) and synthetic
placeholder art, so you can sanity-check the compositing logic offline:

```bash
python test_local.py   # writes test_output.png
```
