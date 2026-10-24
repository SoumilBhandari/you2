#!/usr/bin/env python3
"""Minimum-wall gate for the You2 enclosure.

check.py proves each part is one connected solid. fit.py proves no two parts
share space. docs.py proves the prose matches. None of them can see a wall that
is too thin to print.

A part can pass all three, satisfy every assert, render beautifully, and still
contain a 0.3 mm web that a 0.4 mm nozzle will not produce — the slicer simply
omits it, and you find out when the print comes off the bed with a hole in it.
That is the classic FDM failure and this repo was blind to it.

HOW IT MEASURES

For a sample of facets it fires a ray from the facet, inward along -normal, and
takes the distance to the first surface facing back at it. That distance is the
local wall thickness. It is a ray probe, not a medial axis: it measures across
the wall, which is what the nozzle has to fill.

Two things it deliberately does not do. It does not sample every facet — a
70,000-facet shell against itself is 5e9 ray-triangle tests, so it takes a
stratified sample and says how dense. And it ignores hits beyond MAX_T, because
a ray that escapes into the cavity is not measuring a wall.

    python3 cad/thickness.py cad/you2-revD.scad
    python3 cad/thickness.py cad/you2-revD.scad --parts shell --rays 8000
"""
import argparse, os, struct, subprocess, sys, tempfile, zlib

import numpy as np

DEFAULT_PARTS = ["shell", "faceplate", "base", "mic_clamp", "driver_clamp",
                 "ballast_pod", "ballast_lid", "foot"]

#: A 0.4 mm nozzle lays a ~0.45 mm bead. Two beads is the floor for a wall that
#: carries nothing; three is the floor for one that does. Below FLOOR the
#: slicer starts dropping the feature rather than thinning it.
FLOOR = 0.85
WARN = 1.25
#: Rays longer than this left the wall and are measuring the cavity.
MAX_T = 12.0
EPS = 1e-3


def export(scad, mode, extra):
    out = os.path.join(tempfile.mkdtemp(), f"{mode}.stl")
    cmd = ["openscad", "-o", out, "--export-format=binstl",
           "-D", f'MODE="{mode}"'] + extra + [scad]
    r = subprocess.run(cmd, capture_output=True, text=True)
    err = "\n".join(l for l in (r.stderr or "").splitlines() if "ERROR" in l)
    return (out if os.path.exists(out) else None), err


def load(path):
    with open(path, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        buf = np.frombuffer(f.read(n * 50), dtype=np.uint8)
    rec = buf.reshape(n, 50)
    vals = rec[:, :48].copy().view("<f4").reshape(n, 4, 3)
    normals, tris = vals[:, 0, :], vals[:, 1:, :]
    # Recompute from winding: exported normals are not always unit length.
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    cross = np.cross(e1, e2)
    norm = np.linalg.norm(cross, axis=1, keepdims=True)
    good = norm[:, 0] > 1e-12
    normals = np.where(good[:, None], cross / np.where(norm == 0, 1, norm), normals)
    return tris[good].astype(np.float64), normals[good].astype(np.float64)


def probe(tris, normals, n_rays, rng, worst=1):
    """Thickness at a stratified sample of facets. Returns (t, origin) arrays."""
    areas = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0],
                                    tris[:, 2] - tris[:, 0]), axis=1) / 2
    # Sample proportional to area so a large flat wall is not out-voted by the
    # thousands of tiny facets that tessellate one fillet.
    p = areas / areas.sum()
    idx = rng.choice(len(tris), size=min(n_rays, len(tris)), replace=False,
                     p=p, shuffle=False)
    origins = tris[idx].mean(axis=1) - normals[idx] * EPS
    dirs = -normals[idx]

    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    e1, e2 = v1 - v0, v2 - v0
    best = np.full(len(idx), np.inf)

    # Moller-Trumbore, rays chunked so the (rays x tris) arrays stay in memory.
    chunk = max(1, int(4e7 / max(1, len(tris))))
    for s in range(0, len(idx), chunk):
        o, d = origins[s:s + chunk], dirs[s:s + chunk]
        pv = np.cross(d[:, None, :], e2[None, :, :])
        det = np.einsum("ij,kij->ki", e1, pv)
        # Only surfaces facing back at the ray bound a wall; a facet pointing
        # the same way as travel is the far side of some other feature.
        live = np.abs(det) > 1e-9
        inv = np.where(live, 1.0 / np.where(det == 0, 1, det), 0.0)
        tv = o[:, None, :] - v0[None, :, :]
        u = np.einsum("kij,kij->ki", tv, pv) * inv
        qv = np.cross(tv, e1[None, :, :])
        v = np.einsum("kij,kij->ki", d[:, None, :], qv) * inv
        t = np.einsum("ij,kij->ki", e2, qv) * inv
        hit = live & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > EPS * 10) & (t < MAX_T)
        facing = np.einsum("kj,ij->ki", d, normals) > 0.05
        t = np.where(hit & facing, t, np.inf)
        best[s:s + chunk] = t.min(axis=1)
    return best, origins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scad")
    ap.add_argument("--parts", nargs="*", default=DEFAULT_PARTS)
    ap.add_argument("--rays", type=int, default=4000)
    ap.add_argument("-D", dest="defines", action="append", default=[])
    ap.add_argument("--floor", type=float, default=FLOOR)
    a = ap.parse_args()
    extra = [x for d in a.defines for x in ("-D", d)]
    # One generator seeded PER PART, not one shared across the run. With a
    # shared generator the sample for the fifth part depends on how many rays
    # the first four consumed, so `--parts shell` and a full run probed
    # different places and disagreed about whether the shell was sound. A gate
    # whose answer depends on what else you asked it is not a gate.
    # zlib.crc32, NOT hash(): Python randomises string hashing per process, so
    # seeding from hash() made the sample different on every invocation — the
    # exact non-determinism this is here to remove.
    def gen(tag):
        return np.random.default_rng(zlib.crc32(tag.encode()))
    rng = gen("control")

    # Prove the probe can measure before trusting it: a 2 mm plate must read
    # 2 mm. Without this, a broken ray test reports inf everywhere and every
    # part "passes".
    f = tempfile.NamedTemporaryFile("w", suffix=".scad", delete=False)
    f.write("cube([40, 40, 2], center = true);\n")
    f.close()
    ctl, _ = export(f.name, "x", [])
    os.unlink(f.name)
    if ctl is None:
        print("  HARNESS BROKEN  the control plate did not export")
        return 2
    t, _ = probe(*load(ctl), 400, rng)
    got = np.median(t[np.isfinite(t)]) if np.isfinite(t).any() else float("nan")
    if not (abs(got - 2.0) < 0.05):
        print(f"  HARNESS BROKEN  a 2.00 mm plate measured {got:.3f} mm")
        print("  every 'ok' below would be meaningless — refusing to run")
        return 2
    print(f"  control         a 2.00 mm plate measures {got:.3f} mm")
    print()

    bad = 0
    for part in a.parts:
        stl, err = export(a.scad, part, extra)
        if stl is None:
            print(f"  FAIL  {part:<14} did not export   {err[:70]}")
            bad += 1
            continue
        tris, normals = load(stl)
        t, origins = probe(tris, normals, a.rays, gen(part))
        finite = np.isfinite(t)
        if not finite.any():
            print(f"  ----  {part:<14} no ray crossed a wall under {MAX_T} mm")
            continue
        tt, oo = t[finite], origins[finite]
        lo = tt.min()
        where = oo[tt.argmin()]
        thin = int((tt < a.floor).sum())
        pct = 100.0 * thin / len(tt)
        mark = "FAIL" if lo < a.floor else ("warn" if lo < WARN else "ok  ")
        if mark == "FAIL":
            bad += 1
        print(f"  {mark}  {part:<14} min {lo:5.2f} mm  median {np.median(tt):5.2f}"
              f"  {thin:>4} of {len(tt)} rays under {a.floor} ({pct:.1f} %)")
        if mark != "ok  ":
            order = np.argsort(tt)[:3]
            for k in order:
                print(f"        {tt[k]:5.2f} mm at x={oo[k][0]:7.1f} "
                      f"y={oo[k][1]:7.1f} z={oo[k][2]:7.1f}")
    print()
    print(f"every sampled wall is at least {a.floor} mm" if not bad
          else f"{bad} part(s) have walls a 0.4 mm nozzle will not fill")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
