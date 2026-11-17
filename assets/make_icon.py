import math
def wedge(a1, a2, ro, ri, push, cx, cy):
    mid = math.radians((a1 + a2) / 2)
    ox, oy = cx + push * math.cos(mid), cy + push * math.sin(mid)
    p = lambda a, r: (ox + r * math.cos(math.radians(a)), oy + r * math.sin(math.radians(a)))
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p(a1, ri), p(a1, ro), p(a2, ro), p(a2, ri)
    large = 1 if (a2 - a1) > 180 else 0
    return (f"M{x1:.2f},{y1:.2f} L{x2:.2f},{y2:.2f} A{ro},{ro} 0 {large} 1 {x3:.2f},{y3:.2f} "
            f"L{x4:.2f},{y4:.2f} A{ri},{ri} 0 {large} 0 {x1:.2f},{y1:.2f} Z"), (ox, oy)

def svg(small=False):
    cx, cy = 120, 120                     # disc sits a little up-left; the slice slides down-right
    ro, ri = (92, 20) if small else (88, 17)
    cut = (15, 75)                        # the slice that's pulled out (degrees, 0 = right, clockwise)
    gap = 2.5 if small else 1.6
    push = 20 if small else 17
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs>',
         '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1f2a44"/><stop offset="1" stop-color="#0b1020"/></linearGradient>',
         # iridescent disc: cool sweep top-left to bottom
         '<linearGradient id="disc" x1="0.1" y1="0" x2="0.8" y2="1"><stop offset="0" stop-color="#67e8f9"/>'
         '<stop offset=".45" stop-color="#818cf8"/><stop offset="1" stop-color="#c026d3"/></linearGradient>',
         '<linearGradient id="slice" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fdba74"/><stop offset="1" stop-color="#f43f5e"/></linearGradient>',
         '<radialGradient id="shine" cx=".3" cy=".25" r=".7"><stop offset="0" stop-color="#fff" stop-opacity=".45"/><stop offset=".6" stop-color="#fff" stop-opacity="0"/></radialGradient>',
         '<filter id="sh" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#000" flood-opacity=".45"/></filter>',
         '</defs>',
         '<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#bg)"/>']
    if not small:
        o.append('<rect x="8.5" y="8.5" width="239" height="239" rx="55.5" fill="none" stroke="#fff" stroke-opacity=".09"/>')
    body, _ = wedge(cut[1] + gap, cut[0] + 360 - gap, ro, ri, 0, cx, cy)
    sl, (sx, sy) = wedge(cut[0] + gap, cut[1] - gap, ro, ri, push, cx, cy)
    o.append(f'<path d="{body}" fill="url(#disc)"/>')
    if not small:
        o.append(f'<clipPath id="cb"><path d="{body}"/></clipPath><clipPath id="cs"><path d="{sl}"/></clipPath>')
        for r, op in ((74, .22), (58, .16), (42, .2), (28, .3)):
            o.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#fff" stroke-opacity="{op}" stroke-width="1.6" clip-path="url(#cb)"/>')
        o.append(f'<path d="{body}" fill="url(#shine)"/>')
    o.append(f'<path d="{sl}" fill="url(#slice)" filter="url(#sh)"/>')
    if not small:
        for r, op in ((74, .28), (58, .2), (42, .25), (28, .35)):
            o.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="{r}" fill="none" stroke="#fff" stroke-opacity="{op}" stroke-width="1.6" clip-path="url(#cs)"/>')
    o.append('</svg>')
    return "\n".join(o)
open("icon.svg", "w").write(svg()); open("icon-small.svg", "w").write(svg(True))
