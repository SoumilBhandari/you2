#!/usr/bin/env python3
"""Renders the You2 enclosure.

The images in renders/revD/ had no source. They were made by hand at a shell
prompt, in a session whose scratch directory no longer exists, which means the
only way to change a camera angle was to guess the flags again. The generator
for docs/build.html was lost the same way. So: the views live here.

    python3 cad/render.py                 # everything, into cad/renders/revD/
    python3 cad/render.py hero section    # just those
    python3 cad/render.py --list

Two rules are baked in because both have already cost an afternoon:

  --render, never preview. The CSG tree exceeds OpenSCAD's preview normaliser,
  which aborts and writes a BLANK PNG with no error and exit 0. Every render
  here is checked for a plausible file size afterwards for that reason.

  Deterministic framing. --viewall rescales when the model changes, so two
  renders of two revisions cannot be compared. Distance is given explicitly.
"""
import argparse, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAD = os.path.join(ROOT, "cad", "you2-revD.scad")
OUT = os.path.join(ROOT, "cad", "renders", "revD")
SCHEME = "Tomorrow Night"

#  name        mode        tz    rot x   z    dist  size        extra -D
#  tz is the camera target height: half the object, so it sits centred without
#  --viewall, which would rescale between revisions and make two renders of two
#  designs impossible to compare.
VIEWS = [
    ("d-hero",     "assembly",  96,  62,  28,  700, (1300, 1150), []),
    ("d-front",    "assembly",  96,  90,   0,  620, (1000, 1200), []),
    ("d-profile",  "assembly",  96,  90,  90,  640, (1150, 1150), []),
    ("d-rear",     "assembly",  96,  72, 200,  700, (1200, 1150), []),
    ("d-top",      "assembly",  96,  18,   8,  640, (1150, 1100), []),
    ("d-section",  "section",   96,  74,  38,  720, (1300, 1150), []),
    ("d-exploded", "exploded",  96,  70,  30, 1250, (1150, 1500), []),
    ("d-shell",    "shell",     96,  64,  32,  700, (1200, 1150), []),
    ("d-base",     "base",       5,  58,  25,  520, (1250,  900), []),
]
for n in range(1, 7):
    VIEWS.append((f"d-plate{n}", "printplate", 0, 44, 0, 640, (1200, 900),
                  [f"PLATE={n}"]))


def render(name, mode, tz, rx, rz, dist, size, extra):
    path = os.path.join(OUT, name + ".png")
    cmd = ["openscad", "-o", path, "--render",
           f"--imgsize={size[0]},{size[1]}",
           f"--camera=0,0,{tz},{rx},0,{rz},{dist}",
           f"--colorscheme={SCHEME}", "-D", f'MODE="{mode}"']
    for d in extra:
        cmd += ["-D", d]
    cmd.append(SCAD)
    r = subprocess.run(cmd, capture_output=True, text=True)
    err = [l for l in (r.stderr or "").splitlines() if "ERROR" in l]
    kb = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
    # A blank 1400x1150 PNG of flat background compresses to a few KB. Anything
    # under 8 KB is the preview-normaliser failure, not a render.
    ok = kb >= 8 and not err
    print(f"  {'ok  ' if ok else 'FAIL'} {name:<12} {kb:>4} KB"
          + ("   " + err[0][:90] if err else "")
          + ("   BLANK — did this run with --render?" if kb and kb < 8 else ""))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("views", nargs="*", help="names to render (default: all)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for v in VIEWS:
            print(" ", v[0])
        return 0
    os.makedirs(OUT, exist_ok=True)
    want = [v for v in VIEWS if not a.views or v[0] in a.views
            or v[0].replace("d-", "") in a.views]
    if not want:
        print("no view matched; --list to see them")
        return 2
    bad = sum(not render(*v) for v in want)
    print()
    print(f"{len(want) - bad}/{len(want)} rendered into cad/renders/revD/")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
