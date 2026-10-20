#!/usr/bin/env python3
"""Geometry gate for the You2 enclosure.

`Status: NoError` from OpenSCAD means the mesh is manifold. It does NOT mean
the part is one object. Both rev C and the first cut of rev D exported shells
that were manifold and in several disconnected pieces — internal features
(the speaker chamber, the stiffening frame, the corner bosses) sat a fraction
of a millimetre clear of the wall they were supposed to fuse to, because they
were intersected with a solid slightly SMALLER than the cavity instead of
slightly larger.

Nothing catches that by looking at a render. This does.

It starts by proving it can fail. Every part reporting "1 solid" is also what a
broken component finder prints, and a gate that has only ever passed is not
evidence — so before it looks at the model it runs the union-find over a mesh it
knows is two pieces and one it knows is one, and refuses to grade the model if
either answer is wrong.

    python3 cad/check.py cad/you2-revD.scad
    python3 cad/check.py cad/you2-revD.scad --parts shell faceplate base
"""
import argparse, os, struct, subprocess, sys, tempfile
from collections import Counter, defaultdict

DEFAULT_PARTS = ["shell", "faceplate", "base", "mic_clamp", "driver_clamp",
                 "ballast_pod", "ballast_lid", "mic_gasket", "panel_gasket",
                 "speaker_gasket", "foot"]

# The model has three switches, and only one configuration was ever gated. Two
# of the others were broken: LED_BAR = true exported the shell with a
# 1.7 x 1.2 x 2.0 mm chip floating at each side, while every assert passed and
# the documentation said both variants were clean. A switch nothing runs is a
# switch that does not work.
VARIANTS = [("default", []),
            ("LED_BAR", ["LED_BAR=true"]),
            ("no ballast", ["BALLAST=false"]),
            ("flutes", ["FLUTES=true"]),
            ("LED_BAR + flutes", ["LED_BAR=true", "FLUTES=true"])]


def export(scad, mode, extra):
    out = os.path.join(tempfile.mkdtemp(), f"{mode}.stl")
    cmd = ["openscad", "-o", out, "--export-format=binstl",
           "-D", f'MODE="{mode}"'] + extra + [scad]
    r = subprocess.run(cmd, capture_output=True, text=True)
    err = "\n".join(l for l in (r.stderr or "").splitlines()
                    if "ERROR" in l or "WARNING" in l)
    return (out if os.path.exists(out) else None), err


def components(path):
    """Return [(vertex_count, centroid)] per connected component, largest first."""
    with open(path, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        tris, idx = [], {}
        for _ in range(n):
            d = struct.unpack("<12fH", f.read(50))
            t = []
            for k in (3, 6, 9):
                key = (round(d[k], 4), round(d[k + 1], 4), round(d[k + 2], 4))
                if key not in idx:
                    idx[key] = len(idx)
                t.append(idx[key])
            tris.append(t)
    parent = list(range(len(idx)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for t in tris:
        for a, b in ((t[0], t[1]), (t[1], t[2])):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    pts = {v: k for k, v in idx.items()}
    groups = defaultdict(list)
    for i in range(len(idx)):
        groups[find(i)].append(pts[i])
    out = []
    for root, cnt in sorted(Counter(find(i) for i in range(len(idx))).items(),
                            key=lambda kv: -kv[1]):
        g = groups[root]
        c = tuple(round(sum(p[i] for p in g) / len(g), 1) for i in range(3))
        out.append((cnt, c))
    return out, n


SELF_TEST = [("two separated cubes", "cube(10); translate([50,0,0]) cube(10);", 2),
             ("one cube",            "cube(10);",                              1)]


def self_test():
    """Prove the component finder can tell one solid from two before trusting it."""
    for name, src, want in SELF_TEST:
        f = tempfile.NamedTemporaryFile("w", suffix=".scad", delete=False)
        f.write(src + "\n")
        f.close()
        out = os.path.join(tempfile.mkdtemp(), "t.stl")
        subprocess.run(["openscad", "-o", out, "--export-format=binstl", f.name],
                       capture_output=True)
        os.unlink(f.name)
        got = len(components(out)[0]) if os.path.exists(out) else 0
        if got != want:
            print(f"  HARNESS BROKEN  {name}: found {got} solid(s), expected {want}")
            print("  refusing to grade the model with an instrument that is wrong")
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scad")
    ap.add_argument("--parts", nargs="*", default=DEFAULT_PARTS)
    ap.add_argument("-D", dest="defines", action="append", default=[],
                    help="extra OpenSCAD -D, e.g. -D LED_BAR=true")
    ap.add_argument("--variants", action="store_true",
                    help="run every part under every switch combination")
    a = ap.parse_args()
    extra = [x for d in a.defines for x in ("-D", d)]

    if not self_test():
        return 2

    if a.variants:
        bad = 0
        for name, defs in VARIANTS:
            print(f"  --- {name} " + "-" * (52 - len(name)))
            fails = grade(a.scad, a.parts,
                          extra + [x for d in defs for x in ("-D", d)])
            bad += fails
        print()
        print("every variant builds" if not bad
              else f"{bad} part/variant combination(s) failed")
        return 1 if bad else 0

    bad = grade(a.scad, a.parts, extra)
    print()
    print("self-test passed; all parts are single solids" if not bad
          else f"{bad} part(s) failed — a floating feature will not print")
    return 1 if bad else 0


def grade(scad, parts, extra):
    bad = 0
    for part in parts:
        stl, err = export(scad, part, extra)
        if stl is None:
            print(f"  FAIL  {part:<14} did not export\n        {err}")
            bad += 1
            continue
        comps, facets = components(stl)
        if len(comps) == 1:
            print(f"  ok    {part:<14} 1 solid, {facets} facets")
        else:
            bad += 1
            print(f"  FAIL  {part:<14} {len(comps)} DISCONNECTED SOLIDS")
            for cnt, cen in comps[1:]:
                print(f"        floating piece, {cnt} verts, centroid {cen}")
        if err:
            print(f"        {err}")
    return bad


if __name__ == "__main__":
    sys.exit(main())
