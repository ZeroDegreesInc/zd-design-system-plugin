"""The Color Scales generator: a fifteen-step tint-and-shade ramp per colour family.

The designer's rules (set 2026-10-20), shared with the starter kit's own ramps:
  - Step 5 is the anchor's exact hex. Step 0 sits at HSL lightness 95, step 10 at 18, at the anchor's hue and saturation.
  - Every other variable in the family is pinned onto the step whose base-curve lightness is nearest its own
    (lightest first, one per step, never step 5; within 1.5 of an end it takes that end).
  - Everything else is interpolated between the fixed points: hue on the shortest arc, saturation and lightness linearly.
  - Lightness must fall from step 0 to step 10, or nothing is written for that family.
Two family exceptions: Neutral takes one hue and saturation throughout with no pins, and Yellow spaces its dark half by
OKLCH lightness, with its -400 pinned to step 8.

  python3 -I scales.py check     reproduces the starter kit's reference ramps; every step should match exactly
"""
import colorsys
import math
import sys

STEPS = [0, .5, 1, 1.5, 2, 3, 4, 5, 6, 7, 8, 8.5, 9, 9.5, 10]
TOP, BOT, TOL = 95, 18, 1.5


def hsl(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    H, L, S = colorsys.rgb_to_hls(r, g, b)
    return H * 360, S * 100, L * 100


def from_hsl(H, S, L):
    r, g, b = colorsys.hls_to_rgb(H / 360 % 1, L / 100, S / 100)
    return "#" + "".join(f"{round(x * 255):02X}" for x in (r, g, b))


def base_l(p, La):
    return TOP + (La - TOP) * p / 5 if p <= 5 else La + (BOT - La) * (p - 5) / 5


def lerp_hue(a, b, t):
    d = b - a
    d = d - 360 if d > 180 else d + 360 if d < -180 else d
    return (a + d * t) % 360


# ── OKLab (Björn Ottosson), on linear sRGB ──
lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
gam = lambda c: 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def to_oklch(h):
    r, g, b = (lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l, m, s = (math.copysign(abs(x) ** (1 / 3), x) for x in (.4122214708 * r + .5363325363 * g + .0514459929 * b,
                                                              .2119034982 * r + .6806995451 * g + .1073969566 * b,
                                                              .0883024619 * r + .2817188376 * g + .6299787005 * b))
    L, a, bb = .2104542553 * l + .7936177850 * m - .0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + .4505937099 * s, .0259040371 * l + .7827717662 * m - .8086757660 * s
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def from_oklch(L, C, h):
    """sRGB hex, reducing chroma in 0.002 steps until it's in gamut."""
    while True:
        a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
        l, m, s = ((L + .3963377774 * a + .2158037573 * b) ** 3, (L - .1055613458 * a - .0638541728 * b) ** 3, (L - .0894841775 * a - 1.2914855480 * b) ** 3)
        rgb = (4.0767416621 * l - 3.3077115913 * m + .2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s, -.0041960863 * l - .7034186147 * m + 1.7076147010 * s)
        if all(-1e-9 <= x <= 1 + 1e-9 for x in rgb) or C <= 0:
            return "#" + "".join(f"{round(max(0, min(1, gam(max(0, x)))) * 255):02X}" for x in rgb)
        C = max(0, C - .002)


def ramp(family, anchor, members, neutral_ref=None):
    """(15 hexes, {member hex: step or None}) for a family. members: every variable hex in it, anchor included."""
    Ha, Sa, La = hsl(anchor)
    if family.lower() == "neutral":
        Hn, Sn, _ = hsl(neutral_ref or anchor)
        return [from_hsl(Hn, Sn, base_l(p, La)) for p in STEPS], {}
    yellow = family.lower() == "yellow"
    others = sorted({m.lower() for m in members if m.lower() != anchor.lower()}, key=lambda h: -hsl(h)[2])
    fixed, pins = {0: (Ha, Sa, TOP), 10: (Ha, Sa, BOT), 5: (Ha, Sa, La)}, {}
    if yellow:      # its -400 (the darkest) sits on step 8; the dark half is spaced in OKLCH below
        pins[others[-1]] = 8
        others = others[:-1]
    for v in others:
        H, S, L = hsl(v)
        if abs(L - TOP) <= TOL:
            cands = [0]
        elif abs(L - BOT) <= TOL:
            cands = [10]
        elif L > TOP + TOL or L < BOT - TOL:
            pins[v] = None
            continue
        else:
            cands = [p for p in STEPS if p not in (0, 5, 10) and (not yellow or p < 5)]
        cands = [p for p in cands if p not in pins.values()]
        if not cands:
            pins[v] = None
            continue
        p = min(cands, key=lambda p: abs(base_l(p, La) - L))
        fixed[p] = (H, S, L)
        pins[v] = p
    keys = sorted(fixed)
    out = []
    for p in STEPS:
        if yellow and p > 5:
            out.append(None)
            continue
        if p in fixed:
            out.append(from_hsl(*fixed[p]))
            continue
        lo, hi = max(k for k in keys if k < p), min(k for k in keys if k > p)
        t = (p - lo) / (hi - lo)
        a, b = fixed[lo], fixed[hi]
        out.append(from_hsl(lerp_hue(a[0], b[0], t), a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t))
    if yellow:      # steps 6–10 in OKLCH: anchor → pin at step 8, then the same fall in lightness per unit
        pin = next(h for h, s in pins.items() if s == 8)
        (L5, C5, h5), (L8, C8, h8) = to_oklch(anchor), to_oklch(pin)
        rate = (L8 - L5) / 3
        for i, p in enumerate(STEPS):
            if p <= 5:
                continue
            if p < 8:
                t = (p - 5) / 3
                out[i] = from_oklch(L5 + (L8 - L5) * t, C5 + (C8 - C5) * t, lerp_hue(h5, h8, t))
            elif p == 8:
                out[i] = pin.upper()
            else:
                L = L8 + rate * (p - 8)
                out[i] = from_oklch(L, C8 * L / L8, h8)
    Ls = [hsl(h)[2] for h in out]
    if not all(x > y for x, y in zip(Ls, Ls[1:])):
        raise ValueError(f"{family}: lightness doesn't fall from step 0 to 10; two variables are out of order relative to the anchor")
    return out, pins


REFERENCE = {   # the starter kit, regenerated with these rules on 2026-10-20: (anchor, other members, expected ramp)
    "Brand": ("#6514e6", ["#f0e8fd", "#a372f0", "#3d0c8a"], "F0E8FD E3D4FB D6C0F9 C9ACF7 BC99F5 A372F0 8441ED 6514E6 5811C7 4A0FA9 3D0C8A 370B7D 310A6F 2B0962 250754"),
    "Red": ("#d23737", ["#fce2e2", "#f18787", "#ab2929"], "FCE2E2 F9CBCB F7B4B4 F49E9E F18787 E96A6A DE5050 D23737 C22D2D AB2929 8B2121 7B1D1D 6B1919 5B1515 4B1111"),
    "Yellow": ("#e8c23e", ["#faf1d2", "#f0d780", "#a18626"], "FCF8E8 FAF1D2 F8ECC2 F6E7B1 F4E1A1 F0D780 ECCD5F E8C23E D0AE36 B89A2E A18626 967C23 8A731F 7F6A1C 756019"),
    "Green": ("#27ae60", ["#d2f2e0", "#73cc99", "#17683a"], "EAFAF1 D2F2E0 BEEBD2 ABE4C4 98DDB5 73CC99 42C87B 27AE60 229A55 1E864A 19723F 17683A 155E34 13552F 114B29"),
    "Blue": ("#146fe6", ["#cadffa", "#5496ed", "#0d4794"], "E8F1FD CADFFA B2D0F8 9BC2F5 83B3F2 5496ED 3282EB 146FE6 1262CB 0F54AF 0D4794 0C3F84 0A3874 093064 072954"),
    "Neutral": ("#a6a8a1", [], "F2F3F2 EBEBEA E3E4E2 DCDCDA D4D5D1 C5C6C1 B5B7B1 A6A8A1 8F9188 777A71 5F615A 53554E 474843 3B3C38 2F302C"),
}


def check():
    worst = 0
    for fam, (anchor, others, want) in REFERENCE.items():
        got, _ = ramp(fam, anchor, [anchor] + others)
        d = max(abs(int(g[i:i + 2], 16) - int(w[i - 1:i + 1], 16)) for g, w in zip(got, want.split()) for i in (1, 3, 5))
        worst = max(worst, d)
        print(f"{fam}: max channel difference {d}")
    return worst


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        sys.exit(1 if check() > 1 else 0)
