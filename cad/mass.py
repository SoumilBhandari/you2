#!/usr/bin/env python3
"""Mass plausibility gate for the You2 enclosure.

docs.py checks that the build document's totals equal the sum of its rows. That
is arithmetic, and arithmetic cannot tell which side of a disagreement is wrong.
It once reported that a 674 g headline disagreed with a table summing to 676,
and the headline was "corrected" to 730 — when the headline had been right and
two rows of the table were giving a PAIR mass in a per-piece column.

This checks something arithmetic cannot: **a printed part cannot weigh more than
the same part solid.** Infill removes material; it never adds any. So for every
part, claimed mass <= volume x density, with no slicer, no estimate and no
tolerance to argue about. The ballast pod claimed 49 g against 27 g of solid
PETG — a ratio of 1.82, which is not a tolerance problem, it is impossible.

Volume comes from the mesh by the signed-tetrahedron sum, which is exact for a
closed manifold. check.py already proves every part is one closed solid, so that
precondition is gated rather than assumed.

    python3 cad/mass.py cad/you2-revD.scad
    python3 cad/mass.py cad/you2-revD.scad --floor 0.5

The lower bound is a sanity check in the other direction: a part claiming 20 %
of its solid mass is either a typo or an infill nobody would print.
"""
import argparse, os, re, struct, subprocess, sys, tempfile, html

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "docs", "build.html")

#: g/cm3. PETG 1.27 and TPU 95A 1.21 are the mid-points of the usual spread;
#: both are quoted to 2 dp by every filament vendor and vary by about 2 %.
DENSITY = {"PETG": 1.27, "TPU": 1.21}


def rows():
    """(part, qty, material, claimed grams) from the build document's table."""
    s = open(BUILD, errors="replace").read()
    i = s.index("03 / Printed parts")
    seg = s[i:s.index("04 /", i)]
    out = []
    for r in re.findall(r"<tr>(.*?)</tr>", seg, re.S):
        c = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
             for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if len(c) < 6 or not re.fullmatch(r"\d+", c[1]) or c[2] not in DENSITY:
            continue
        m = re.match(r"([\d.]+) ?g", c[5])
        if not m:
            continue
        # the cell holds the part name, then an "optional" tag, then the
        # description run straight on: "ballast pod optionalOptional. 63 cm3..."
        name = re.split(r"(?<=[a-z])(?=[A-Z])", c[0])[0].strip().lower()
        name = re.sub(r"\s+optional$", "", name)
        out.append((name.replace(" ", "_"), int(c[1]), c[2], float(m.group(1))))
    return out


def modes(scad):
    """The MODE names the model actually dispatches on.

    Worth checking, because the dispatch ends in `else assembly(0)`: an
    unrecognised MODE does not error, it silently renders the WHOLE DEVICE. A
    mistyped part name therefore measures 918 g and reports a plausible-looking
    ratio instead of failing. check.py has the same exposure."""
    src = open(scad, errors="replace").read()
    return set(re.findall(r'MODE == "([a-z_]+)"', src))


def volume_cm3(stl):
    """Exact for a closed manifold: sum of signed tetrahedra to the origin."""
    with open(stl, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        buf = np.frombuffer(f.read(n * 50), dtype=np.uint8).reshape(n, 50)
    v = buf[:, :48].copy().view("<f4").reshape(n, 4, 3)[:, 1:, :].astype(np.float64)
    a, b, c = v[:, 0], v[:, 1], v[:, 2]
    return abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6) / 1000


def export(scad, mode, extra):
    out = os.path.join(tempfile.mkdtemp(), f"{mode}.stl")
    subprocess.run(["openscad", "-o", out, "--export-format=binstl",
                    "-D", f'MODE="{mode}"'] + extra + [scad], capture_output=True)
    return out if os.path.exists(out) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scad")
    ap.add_argument("--floor", type=float, default=0.45,
                    help="minimum plausible claimed/solid ratio")
    ap.add_argument("-D", dest="defines", action="append", default=[])
    a = ap.parse_args()
    extra = [x for d in a.defines for x in ("-D", d)]

    # Prove the volume measurement before trusting it: a 20 mm cube is 8 cm3.
    f = tempfile.NamedTemporaryFile("w", suffix=".scad", delete=False)
    f.write("cube(20, center = true);\n")
    f.close()
    ctl = export(f.name, "x", [])
    os.unlink(f.name)
    got = volume_cm3(ctl) if ctl else float("nan")
    if not abs(got - 8.0) < 0.001:
        print(f"  HARNESS BROKEN  a 20 mm cube measured {got:.4f} cm3, expected 8")
        return 2
    print(f"  control         a 20 mm cube measures {got:.3f} cm3")
    print()

    table = rows()
    if not table:
        print("  could not read the parts table out of docs/build.html")
        return 2

    known = modes(a.scad)
    unknown = [n for n, _, _, _ in table if n not in known]
    if unknown:
        print(f"  the table names parts the model does not dispatch: {unknown}")
        print("  an unknown MODE renders the whole assembly instead of failing,")
        print("  so measuring one would silently compare against 918 g")
        return 2

    print(f"  {'part':<16}{'qty':>4}{'solid g':>9}{'claimed':>9}{'ratio':>8}")
    bad = 0
    for name, qty, mat, claimed in table:
        stl = export(a.scad, name, extra)
        if stl is None:
            print(f"  {name:<16} did not export — is MODE=\"{name}\" a real part?")
            bad += 1
            continue
        solid = volume_cm3(stl) * DENSITY[mat]
        # The table quotes whole grams, so a 1 g cell means 0.5-1.5 g. Compare
        # against the bottom of that interval or every rounded-up small part
        # reads as impossible.
        floor_claim = claimed - 0.5 if claimed == int(claimed) else claimed
        ratio = claimed / solid if solid else float("inf")
        mark = ("IMPOSSIBLE" if floor_claim > solid else
                "low" if ratio < a.floor else "ok")
        if mark != "ok":
            bad += 1
        print(f"  {name:<16}{qty:>4}{solid:9.1f}{claimed:9.1f}{ratio:8.2f}  {mark}")
        if mark == "IMPOSSIBLE":
            print(f"        a printed part cannot outweigh itself solid. If the "
                  f"table's qty is {qty}, is {claimed:.0f} g the PAIR?")
    print()
    print("  every claimed mass is physically possible" if not bad
          else f"  {bad} part(s) claim a mass no printer could produce")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
