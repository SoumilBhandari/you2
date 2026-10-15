#!/usr/bin/env python3
"""Prose gate for the You2 enclosure.

check.py proves each part is one connected solid. fit.py proves no two parts
occupy the same space. Neither of them reads a word of English, and every defect
found in this project since rev D landed has been a sentence:

  · "14 asserts" in four places, when the model has had more for weeks
  · "147 cm2 of bed contact", measured with a method that counted fillets
  · "two Ø8 x 27 mm cantilevers", which were Ø10 x 24
  · "Ten parts, two materials, six plates" over a table listing eleven
  · a bill of materials buying a two-microphone board for a four-mic enclosure

None of that is sloppiness you can fix by being careful. The numbers are derived
from a model that keeps moving, they are repeated across six documents, and a
human re-deriving them by hand is exactly the step that failed. So: derive them
from the model, once, and make disagreement an error.

    python3 cad/docs.py            # the whole repo
    python3 cad/docs.py --quick    # skip the two facts that need a mesh export
    python3 cad/docs.py --facts    # just print the ground truth and stop

Adding a claim: put the number in FACTS with a derivation, and a pattern in
CHECKS. If you cannot derive it from the model, it does not belong here — a
hand-maintained expected value is the thing this file exists to replace.
"""
import argparse, math, os, re, struct, subprocess, sys, tempfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAD = os.path.join(ROOT, "cad", "you2-revD.scad")
DOCS = ["README.md", "HANDOFF.md", "handoff_hardware.md", "cad/README.md",
        "docs/build.html", "docs/hardware-plan.md", "hardware/README.md",
        "hardware/assembly.md", "hardware/wiring.md", "hardware/measure-first.md"]

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
         "twenty": 20}


def num(s):
    s = s.strip().lower().replace(",", "")
    return WORDS[s] if s in WORDS else float(s)


# --------------------------------------------------------------------------
#  GROUND TRUTH — every value below is read out of the model, never typed here
# --------------------------------------------------------------------------
def echo_facts():
    """Run the model and read its own echo() lines."""
    out = os.path.join(tempfile.mkdtemp(), "e.echo")
    subprocess.run(["openscad", "-o", out, "--export-format=echo",
                    "-D", 'MODE="shell"', SCAD], capture_output=True)
    txt = open(out).read()
    m = re.search(r"OVERALL\s*=\s*(\d+) x (\d+) x (\d+)", txt)
    s = re.search(r"skirt\s*=\s*([\d.]+) mm, driver centre z = ([\d.]+)", txt)
    return {"W": int(m.group(1)), "D": int(m.group(2)), "H": int(m.group(3)),
            "skirt": float(s.group(1)), "spk_z": float(s.group(2))}


def source_facts():
    src = open(SCAD).read()
    disp = src[src.index('if      (MODE == "assembly")'):]
    body = src[:src.index('if      (MODE == "assembly")')]
    chk = open(os.path.join(ROOT, "cad", "check.py")).read()
    parts = re.search(r"DEFAULT_PARTS = \[(.*?)\]", chk, re.S).group(1)

    def param(name):
        m = re.search(r"^%s\s*=\s*([\d.]+)" % re.escape(name), body, re.M)
        return float(m.group(1))

    return {"asserts": len(re.findall(r"\bassert\s*\(", body)),
            "modes": len(re.findall(r"MODE ==", disp)),
            "parts": len(re.findall(r'"', parts)) // 2,
            "plates": max(int(x) for x in re.findall(r"PLATE == (\d+)", src)),
            "rake": param("rake"),
            # The cantilevers the driver webs replaced: boss_od across, and
            # drv_reach less the wall they were rooted in, along.
            "cant_d": param("boss_od"),
            "cant_l": (param("wall") + param("seal_land") + param("spk_gask") * 0.8
                       + param("spk_bz")) - param("wall")}


def mesh_facts(H):
    """Bed contact and overhang, measured in the model's own plate-1 orientation:
       printplate() puts the shell there with rotate([180,0,0]) translate([0,0,-H]),
       so (x, y, z) -> (x, -y, H - z). Angles are from vertical: a wall is 0 deg,
       a horizontal ceiling is 90."""
    out = os.path.join(tempfile.mkdtemp(), "shell.stl")
    subprocess.run(["openscad", "-o", out, "--export-format=binstl",
                    "-D", 'MODE="shell"', SCAD], capture_output=True)
    with open(out, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        tris = [struct.unpack("<12fH", f.read(50)) for _ in range(n)]

    def T(p):
        return (p[0], -p[1], H - p[2])

    bed, sel = 0.0, []
    for d in tris:
        A, B, C = T(d[3:6]), T(d[6:9]), T(d[9:12])
        u = [B[i] - A[i] for i in range(3)]
        v = [C[i] - A[i] for i in range(3)]
        cz = u[0] * v[1] - u[1] * v[0]
        cx = u[1] * v[2] - u[2] * v[1]
        cy = u[2] * v[0] - u[0] * v[2]
        area = 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        if area <= 0:
            continue
        nz = cz / (2 * area)
        if nz >= 0:
            continue
        on_bed = max(A[2], B[2], C[2]) < 0.01 and nz < -0.99
        if on_bed:
            bed += area
        elif math.degrees(math.asin(min(1.0, -nz))) > 60:
            sel.append(((A, B, C), area))

    # largest CONNECTED downfacing patch — the number that decides whether it
    # needs support is the size of one span, not the total across the part
    idx, par = {}, []

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    def k(p):
        return (round(p[0], 3), round(p[1], 3), round(p[2], 3))

    for tri, _ in sel:
        for p in tri:
            if k(p) not in idx:
                idx[k(p)] = len(par)
                par.append(len(par))
    for tri, _ in sel:
        ks = [idx[k(p)] for p in tri]
        for x in ks[1:]:
            ra, rb = find(ks[0]), find(x)
            if ra != rb:
                par[ra] = rb
    patch = defaultdict(float)
    for tri, a in sel:
        patch[find(idx[k(tri[0])])] += a
    return {"bed_cm2": bed / 100,
            "down_cm2": sum(a for _, a in sel) / 100,
            "patch_cm2": (max(patch.values()) if patch else 0.0) / 100}


def facts(quick=False):
    f = dict(echo_facts())
    f.update(source_facts())
    f.update({"bed_cm2": None, "down_cm2": None, "patch_cm2": None}
             if quick else mesh_facts(f["H"]))
    return f


# --------------------------------------------------------------------------
#  CLAIMS — (label, pattern, how to check the captured text)
#  Patterns are deliberately tight. A loose one produces false alarms, and a
#  gate that cries wolf gets switched off, which is worse than not having it.
# --------------------------------------------------------------------------
# Markdown and HTML both put emphasis between a number and the noun it counts:
# "Ø8 × 27 mm *horizontal cantilevers*", "<b>Ø8 × 27 mm</b> cantilever". Two real
# claims hid behind exactly that, so every pattern joins with this instead of a
# plain space.
SEP = r"[\s*_`]*(?:</?[a-z]+>)?[\s*_`]*"


def CHECKS(f):
    def eq(key, tol=0.0):
        return (lambda got: abs(num(got) - f[key]) <= tol,
                lambda: f"{f[key]:.1f}" if isinstance(f[key], float) else f[key])

    return [
        ("assert count",
         r"(\d+|[a-z]+)[ -](?:in-model )?asserts?\b", eq("asserts")),
        ("printed-part count",
         r"(\d+|[a-z]+) printed parts\b", eq("parts")),
        ("parts in a heading",
         r"<h3>(\d+|[A-Za-z]+) parts, two materials", eq("parts")),
        ("plate count",
         r"(\d+|[a-z]+) plates\b", eq("plates")),
        # A bare "N x N x N" also describes the bed (220x220x250), the driver
        # box (50x45x22) and rev C (134x125x165), none of which are wrong. The
        # discriminator is overlap: a triple sharing two values with the device
        # and differing in the third is a stale device dimension; one sharing
        # none is a different object and none of this gate's business.
        ("overall size",
         r"(\d+ [x×] \d+ [x×] \d+)(?: mm)?",
         (lambda got, _d=[f["W"], f["D"], f["H"]]:
             (lambda t: t == _d or sum(a == b for a, b in zip(t, _d)) < 2)(
                 [int(x) for x in re.findall(r"\d+", got)]),
          lambda: f"{f['W']} × {f['D']} × {f['H']}")),
        ("bed contact",
         r"([\d.]+) ?cm.? of bed contact", eq("bed_cm2", 0.05)),
        ("bed contact, restated",
         r"[Bb]ed contact is ([\d.]+) ?cm", eq("bed_cm2", 0.05)),
        ("downfacing area",
         r"([\d.]+) ?cm.? facing down(?:ward)?", eq("down_cm2", 0.05)),
        ("largest downfacing patch",
         r"([\d.]+) ?cm.?" + SEP + r"(?:is a|of)" + SEP + r"[\d.]+ ?mm [\w -]*?ledge",
         eq("patch_cm2", 0.05)),
        ("driver cantilever diameter",
         r"Ø([\d.]+) [x×] [\d.]+ mm" + SEP + r"(?:horizontal )?cantilever",
         eq("cant_d", 0.05)),
        ("driver cantilever length",
         r"Ø[\d.]+ [x×] ([\d.]+) mm" + SEP + r"(?:horizontal )?cantilever",
         eq("cant_l", 0.05)),
        ("rake angle",
         r"(\d+)° raked face", eq("rake")),
    ]


def scan(f, files):
    bad = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        # Whole file at once, not line by line: markdown wraps, and
        # "Ø8 × 27 mm\nhorizontal cantilevers" is the same claim as the one-line
        # version. Scanning per line silently missed exactly that instance.
        # docs/design.md is deliberately excluded: section 7 preserves rev C's
        # numbers under a note saying so. A gate cannot tell quotation from
        # assertion, so that file is quarantined rather than silently wrong.
        text = open(path, errors="replace").read()
        flat = text.replace("\n", " ")
        for label, pat, (ok, want) in CHECKS(f):
            for m in re.finditer(pat, flat):
                got = m.group(1)
                try:
                    good = ok(got)
                except (KeyError, ValueError, TypeError):
                    continue              # unparseable capture, not a claim
                if not good:
                    line = text.count("\n", 0, m.start()) + 1
                    bad.append((rel, line, label, " ".join(m.group(0).split()), want()))
    return sorted(set(bad))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="skip the facts that need a mesh export (~20 s)")
    ap.add_argument("--facts", action="store_true", help="print ground truth and stop")
    a = ap.parse_args()

    f = facts(quick=a.quick)
    if a.facts:
        for k, v in f.items():
            print(f"  {k:<12} {v}")
        return 0

    skip = {"bed contact", "bed contact, restated", "downfacing area",
            "largest downfacing patch"} if a.quick else set()
    bad = [b for b in scan(f, DOCS) if b[2] not in skip]
    for rel, lineno, label, text, want in bad:
        print(f"  WRONG  {rel}:{lineno}")
        print(f"         {label}: says {text!r}, model says {want}")
    print()
    if a.quick:
        print("(--quick: the four mesh-derived claims were not checked)")
    print(f"{len(bad)} claim(s) disagree with the model" if bad
          else f"every claim in {len(DOCS)} documents agrees with the model")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
