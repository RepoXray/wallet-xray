"""Wallet Xray share card (1080x1080 PNG), matching the site's square card:
logo, brain-scan thumbnail coloured by subtest heat, both IQs, profile, subtest bars."""
import io, math, os, random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
BG, PANEL, LINE = (11, 13, 12), (17, 21, 19), (34, 42, 37)
HANDLE = "walletxray.xyz"   # bot.py sets this to @<bot username> at startup
INK, BONE, MUTED = (241, 245, 242), (185, 198, 190), (123, 138, 129)
PINK, HOT1, HOT2, COLD, VIOLET, LAV = (16, 196, 106), (34, 212, 122), (228, 255, 208), (255, 90, 78), (182, 243, 107), (182, 243, 107)
TESTS = ["SPD", "CON", "LCK", "MEM", "INT"]


def font(name, size, weight):
    f = ImageFont.truetype(os.path.join(FD, name + ".ttf"), size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def heat(v):
    if v is None:
        return VIOLET
    if v >= 60:
        t = (v - 60) / 40
        return tuple(int(HOT1[i] + (HOT2[i] - HOT1[i]) * t) for i in range(3))
    if v <= 40:
        return COLD
    return LAV


def scaled(v):
    return None if v is None else round(1 + v * 18 / 100)


def brain(w, h, s):
    """Folded-cortex thumbnail: random walks inside a two-lobe silhouette, coloured by region."""
    img = Image.new("RGB", (w, h), (7, 9, 8))
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([w * .12, h * .1, w * .88, h * .86], fill=(19, 40, 28))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(60)), .9)
    lay = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(lay)
    cx, cy, rx, ry = w * .48, h * .46, w * .42, h * .3

    def inside(x, y):
        nx, ny = (x - cx) / rx, (y - cy) / ry
        return nx * nx + ny * ny * (1.25 if ny > 0 else 1) < 1

    rnd = random.Random(11)
    for _ in range(260):
        x, y = cx + rnd.uniform(-rx, rx), cy + rnd.uniform(-ry, ry)
        if not inside(x, y):
            continue
        ang, pts = rnd.uniform(0, 6.28), [(x, y)]
        for _ in range(rnd.randint(14, 40)):
            ang += rnd.uniform(-.9, .9)
            nx, ny = pts[-1][0] + math.cos(ang) * 7, pts[-1][1] + math.sin(ang) * 7
            if not inside(nx, ny):
                break
            pts.append((nx, ny))
        if len(pts) < 4:
            continue
        region = min(4, int((pts[0][0] - (cx - rx)) / (2 * rx) * 5))   # 5 vertical regions
        col = heat(s[region])
        d.line(pts, fill=col, width=2, joint="curve")
    # cerebellum + stem
    cb = heat(s[4])
    for i in range(8):                                  # cerebellum folia, lower back
        d.arc([cx + rx * .35, cy + ry * .45 + i * 4, cx + rx * .95, cy + ry * 1.1 + i * 4], 15, 165, fill=cb, width=2)
    d.line([(cx + rx * .18, cy + ry * .95), (cx + rx * .12, cy + ry * 1.45)], fill=(60, 80, 66), width=6)   # short brainstem
    bloom = lay.filter(ImageFilter.GaussianBlur(6))
    out = Image.composite(lay, img, lay.convert("L").point(lambda p: 255 if p > 10 else 0))
    return Image.blend(out, Image.eval(Image.merge("RGB", [a for a in bloom.split()]), lambda p: p), .25)


def render(w):
    W = H = 1080
    im = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-200, -500, W + 200, 500], fill=(19, 58, 36))
    im = Image.blend(im, glow.filter(ImageFilter.GaussianBlur(120)), .8)
    d = ImageDraw.Draw(im)
    P = 64
    # logo: 3x3 matrix with missing tile
    for r in range(3):
        for c in range(3):
            x, y = P + c * 17, P + r * 17
            if r == 2 and c == 2:
                d.rectangle([x + 2, y + 2, x + 12, y + 12], outline=VIOLET, width=2)
            else:
                d.rectangle([x, y, x + 13, y + 13], fill=PINK)
    d.text((P + 66, P - 4), "WALLET XRAY", font=font("Unbounded", 40, "ExtraBold"), fill=INK)
    tag = "EXAMPLE" if w.get("example") else (w.get("chain") or "BNB CHAIN").upper()
    ft = font("JetBrainsMono", 24, "Regular")
    d.text((W - P - d.textlength(tag, font=ft), P + 6), tag, font=ft, fill=MUTED)

    # thumbnail
    panel = Image.new("RGBA", (440, 440), (7, 9, 8, 255))
    gl = Image.new("RGBA", (440, 440), (0, 0, 0, 0))
    ImageDraw.Draw(gl).ellipse([60, 60, 380, 380], fill=(19, 58, 36, 255))
    panel.alpha_composite(gl.filter(ImageFilter.GaussianBlur(70)))
    panel.alpha_composite(site_brain(w.get("profile", "average"), 440, 440))
    mask = Image.new("L", (440, 440), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, 439, 439], radius=28, fill=255)
    im.paste(panel.convert("RGB"), (P, 150), mask)

    # numbers
    x0, ghost = P + 490, w.get("iq") is None
    d.text((x0, 150), "WALLET IQ", font=font("JetBrainsMono", 24, "Regular"), fill=MUTED)
    big = "??" if ghost else str(w["iq"])
    fbig = font("Unbounded", 190, "ExtraBold")
    d.text((x0 - 8, 168), big, font=fbig, fill=VIOLET if ghost else INK)
    if w.get("star") and not ghost:
        d.text((x0 + d.textlength(big, font=fbig), 180), "*", font=font("Unbounded", 90, "ExtraBold"), fill=HOT1)
    d.text((x0, 400), "YOUR IQ", font=font("JetBrainsMono", 26, "Regular"), fill=MUTED)
    you = "??" if ghost else str(w["you"])
    d.text((x0 + 150, 378), you, font=font("Unbounded", 64, "ExtraBold"),
           fill=VIOLET if ghost else (COLD if w["you"] < 90 else INK))
    tone = {"hot": (125, 240, 168), "cold": COLD, "ghost": VIOLET}.get(w.get("tone"), INK)
    d.text((x0, 478), w["en"], font=font("Unbounded", 50, "SemiBold"), fill=tone)
    d.text((x0, 552), w["addr"].replace("…", "..."), font=font("JetBrainsMono", 30, "Regular"), fill=BONE)

    # subtest bars
    y, fb, fv = 634, font("JetBrainsMono", 26, "Medium"), font("JetBrainsMono", 26, "Regular")
    for i, k in enumerate(TESTS):
        v = w["s"][i]
        d.text((P, y + i * 52), k, font=fb, fill=MUTED)
        d.rounded_rectangle([P + 100, y + i * 52 + 8, W - P - 80, y + i * 52 + 26], radius=4, fill=LINE)
        if v is not None:
            d.rounded_rectangle([P + 100, y + i * 52 + 8, P + 100 + (W - 2 * P - 180) * v / 100, y + i * 52 + 26], radius=4, fill=heat(v))
        sv = "??" if v is None else str(scaled(v))
        d.text((W - P - d.textlength(sv, font=fv), y + i * 52), sv, font=fv, fill=INK)

    dx = w["dx"][0] if isinstance(w["dx"], list) else w["dx"]
    d.text((P, H - 160), dx, font=font("Manrope", 30, "SemiBold"), fill=BONE)
    d.line([P, H - 104, W - P, H - 104], fill=LINE, width=2)
    ff = font("JetBrainsMono", 24, "Regular")
    d.text((P, H - 84), "Wallet Xray · not financial advice", font=ff, fill=MUTED)
    d.text((W - P - d.textlength(HANDLE, font=ff), H - 84), HANDLE, font=ff, fill=PINK)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


BRAINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brains")
BRAIN_FOR = {"below": "lucky", "genius": "genius", "sharp": "sharp", "average": "average", "lucky": "lucky",
             "predator": "predator", "ghost": "ghost"}


def site_brain(profile, w, h):
    """The website's own 3D brain (captured per profile from walletxray.xyz), RGBA, fitted into w x h."""
    f = os.path.join(BRAINS, BRAIN_FOR.get(profile, "average") + ".png")
    im = Image.open(f).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 90 else 0).getbbox() or (0, 0) + im.size
    pad = 40
    im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
    k = min(w / im.width, h / im.height)
    im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    fade = Image.new("L", im.size, 0)                      # soft oval edge, no visible crop box
    ImageDraw.Draw(fade).ellipse([im.width * .04, im.height * .04, im.width * .96, im.height * .96], fill=255)
    fade = fade.filter(ImageFilter.GaussianBlur(min(im.size) * .08))
    im.putalpha(Image.composite(im.getchannel("A"), Image.new("L", im.size, 0), fade))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(im, ((w - im.width) // 2, (h - im.height) // 2))
    return out


def _bg(W, H, glow_box, glow_col=(19, 58, 36), blur=120):
    im = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse(glow_box, fill=glow_col)
    return Image.blend(im, glow.filter(ImageFilter.GaussianBlur(blur)), .85)


def _logo(d, x, y, cell, gap):
    for r in range(3):
        for c in range(3):
            xx, yy = x + c * (cell + gap), y + r * (cell + gap)
            if r == 2 and c == 2:
                k = max(2, cell // 6)
                d.rectangle([xx + k, yy + k, xx + cell - k, yy + cell - k], outline=VIOLET, width=k)
            else:
                d.rectangle([xx, yy, xx + cell, yy + cell], fill=PINK)


def _png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def banner(title, sub, tag="BNB CHAIN UTILITY", accent=None, profile="genius", W=1280, H=640):
    """Wide brand banner: logo, two-line headline (second line in accent), subline, brain on the right."""
    im = _bg(W, H, [W * .45, -H * .4, W * 1.3, H * 1.2])
    d = ImageDraw.Draw(im)
    P = 72
    _logo(d, P, P, 15, 4)
    d.text((P + 72, P - 6), "WALLET XRAY", font=font("Unbounded", 34, "ExtraBold"), fill=INK)
    ft = font("JetBrainsMono", 20, "Regular")
    tw = d.textlength(tag, font=ft)
    d.rounded_rectangle([P, P + 92, P + tw + 28, P + 130], radius=6, outline=(26, 110, 64), width=2)
    d.text((P + 14, P + 99), tag, font=ft, fill=(55, 216, 132))
    fh = font("Unbounded", 66, "ExtraBold")
    y = P + 172
    for i, line in enumerate(title):
        d.text((P - 4, y), line, font=fh, fill=(accent or HOT1) if i == len(title) - 1 and len(title) > 1 else INK)
        y += 84
    d.text((P, y + 18), sub, font=font("Manrope", 30, "SemiBold"), fill=BONE)
    rgba = im.convert("RGBA")
    rgba.alpha_composite(site_brain(profile, 600, 470), (W - 640, (H - 470) // 2 + 10))
    im = rgba.convert("RGB")
    return _png(im)


def welcome():
    return banner(["Know before", "you copy."], "Paste a wallet. Get its IQ and yours.", profile="predator")


def list_banner(predators):
    if predators:
        return banner(["This week's", "Predators."], "They sell into the people copying them.", tag="PREDATOR LIST",
                      accent=COLD, profile="predator")
    return banner(["This week's", "Masterminds."], "Skill you keep when you copy it.", tag="MASTERMIND LIST",
                  profile="genius")


def avatar(S=640):
    im = _bg(S, S, [-S * .2, -S * .2, S * 1.2, S * 1.2], (16, 70, 40), 90)
    d = ImageDraw.Draw(im)
    cell, gap = 120, 26
    tot = 3 * cell + 2 * gap
    _logo(d, (S - tot) // 2, (S - tot) // 2, cell, gap)
    return _png(im)


if __name__ == "__main__":
    import json, sys
    data = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples.json")))
    pick = sys.argv[1] if len(sys.argv) > 1 else "predator"
    w = next(x for x in data["wallets"] if x["profile"] == pick or x["id"] == pick)
    open(f"card_{pick}.png", "wb").write(render(w))
    print(f"card_{pick}.png")
