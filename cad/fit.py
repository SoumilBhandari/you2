#!/usr/bin/env python3
"""Interference gate for the You2 enclosure.

`check.py` proves each part is one connected solid. This proves the parts do
not occupy the same space as each other — which is a different failure, and one
you cannot see in a render because the parts are drawn in different colours and
happily pass through one another.

It found four real problems in rev D that renders and asserts both missed:

  · the microphone array overhung the front edge of the top face by 2 mm
  · the mic clamp bosses were 3 mm too long and sat inside the clamp ring
  · the ballast pods fouled the internal cable clamp
  · the vertical side ribs ran down into the base plate's own space

Usage:
    python3 cad/fit.py cad/you2-revD.scad
    python3 cad/fit.py cad/you2-revD.scad -D LED_BAR=true

A result under 0.01 cm3 is clear. Between 0.01 and 0.05 is a coplanar contact —
two faces touching, which is what a gasket seat or a screw boss is supposed to
do. Above that is real interference.
"""
import argparse, os, struct, subprocess, sys, tempfile

PODS = "union(){ for(px=pod_x()) translate([px,pi_y,base_t+5]) ballast_pod(); }"
FACEPLATE = "on_face(fp_c) translate([0,0,-fp_rebate]) faceplate()"
DRIVER_CLAMP = ("on_skirt(spk_z) translate([0,0,-(wall+spk_bz+seal_land)]) "
                "rotate([180,0,0]) driver_clamp()")
MIC_CLAMP = "translate([0,mic_inset_y,H-wall-mic_stack]) mic_clamp_ring()"
USBC_PLUG = "translate([-39,pi_y-34,base_t+6+standoff_h]) cube([13,16,11])"

PAIRS = [
    ("ballast pods  ·  Pi board",        PODS, "proxy_pi();"),
    ("ballast pods  ·  USB-C plug",      PODS, USBC_PLUG + ";"),
    ("ballast pods  ·  shell",           PODS, "shell();"),
    ("ballast pods  ·  base",            PODS, "base();"),
    ("Pi board  ·  shell",               "proxy_pi();", "shell();"),
    ("Pi board  ·  base",                "proxy_pi();", "base();"),
    ("driver box  ·  base",              "proxy_driver();", "base();"),
    ("driver clamp  ·  base",            DRIVER_CLAMP + ";", "base();"),
    ("driver clamp  ·  ballast pods",    DRIVER_CLAMP + ";", PODS),
    ("mic clamp  ·  shell",              MIC_CLAMP + ";", "shell();"),
    ("mic array  ·  shell",              "proxy_mics();", "shell();"),
    ("display module  ·  shell",         "proxy_display();", "shell();"),
    ("faceplate  ·  shell",              FACEPLATE + ";", "shell();"),
    ("base  ·  shell",                   "base();", "shell();"),
]


def volume(lib, expr, defines):
    src = lib + "\n" + expr + "\n"
    f = tempfile.NamedTemporaryFile("w", suffix=".scad", delete=False)
    f.write(src)
    f.close()
    out = os.path.join(tempfile.mkdtemp(), "i.stl")
    subprocess.run(["openscad", "-o", out, "--export-format=binstl"] + defines + [f.name],
                   capture_output=True)
    os.unlink(f.name)
    if not os.path.exists(out):
        return 0.0            # empty intersection — OpenSCAD writes no file
    with open(out, "rb") as g:
        g.read(80)
        n = struct.unpack("<I", g.read(4))[0]
        v = 0.0
        for _ in range(n):
            d = struct.unpack("<12fH", g.read(50))
            a, b, c = d[3:6], d[6:9], d[9:12]
            v += (a[0]*(b[1]*c[2]-b[2]*c[1]) - a[1]*(b[0]*c[2]-b[2]*c[0])
                  + a[2]*(b[0]*c[1]-b[1]*c[0])) / 6
    return abs(v) / 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scad")
    ap.add_argument("-D", dest="defines", action="append", default=[])
    a = ap.parse_args()
    defines = [x for d in a.defines for x in ("-D", d)]

    body = open(a.scad).read()
    marker = 'if      (MODE == "assembly")'
    if marker not in body:
        print("cannot find the MODE dispatch block — is this the right file?")
        return 2
    lib = body[:body.index(marker)]        # everything except the top-level dispatch

    sanity = volume(lib, "// nothing", defines)
    if sanity != 0.0:
        print(f"harness broken: an empty model measured {sanity} cm3")
        return 2

    bad = 0
    for name, x, y in PAIRS:
        v = volume(lib, f"intersection(){{ {x} {y} }}", defines)
        if v < 0.01:
            status = "clear"
        elif v < 0.05:
            status = "contact"
        else:
            status = "*** INTERFERENCE ***"
            bad += 1
        print(f"  {name:<32}{v:>8.3f} cm3   {status}")
    print()
    print("no interference" if not bad else f"{bad} interference(s) — parts occupy the same space")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
