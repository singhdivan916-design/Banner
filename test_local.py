import json
import sys
sys.path.insert(0, ".")
import app as ffapp
from PIL import Image, ImageDraw

SAMPLE_JSON = json.loads(open("sample_player.json").read())


def fake_fetch_player_info(uid):
    return SAMPLE_JSON


def fake_fetch_item_image(item_id):
    if not item_id:
        return None
    # Synthesize a plausible stand-in image per item so we can verify the
    # compositing/layout logic without real network access to the CDN.
    import random
    random.seed(str(item_id))
    w = h = 600
    img = Image.new("RGBA", (w, h), (
        random.randint(30, 90), random.randint(30, 90), random.randint(60, 160), 255))
    d = ImageDraw.Draw(img)
    for i in range(6):
        d.line([(0, i * 100), (w, i * 100 - 200)], fill=(200, 40, 40, 255), width=18)
    return img


ffapp.fetch_player_info = fake_fetch_player_info
ffapp.fetch_item_image = fake_fetch_item_image

img = ffapp.build_banner("14709492693")
img.save("test_output.png")
print("saved", img.size)
