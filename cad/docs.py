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
import argparse, importlib.util, math, os, re, struct, subprocess, sys, tempfile
from urllib.parse import unquote
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
         "twenty": 20, "twenty-one": 21, "twenty-two": 22, "twenty-three": 23,
         "twenty-four": 24, "twenty-five": 25}


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

    # How many gates there are, asked of the gates. Every one of them opens
    # "<something> gate for the You2 enclosure." in its docstring, and
    # render.py -- which is not a gate -- opens "Renders the You2 enclosure."
    # so it falls out on its own. Counting the files rather than keeping a
    # number here is the whole policy of this file: four documents disagreed
    # about this one, the README's own section header among them, and all four
    # were written at a moment when they were right.
    cad = os.path.join(ROOT, "cad")
    gates = [n for n in sorted(os.listdir(cad)) if n.endswith(".py")
             # re.search with re.M, not re.match: the docstring is on line 2,
             # behind the shebang. Anchored at position 0 this counted zero
             # gates, which did not fail -- it quietly declared all four of
             # the documents below wrong against a ground truth of nothing.
             and re.search(r'^"""\w[\w -]* gate for the You2 enclosure\.',
                           open(os.path.join(cad, n), errors="replace").read(400),
                           re.M)]
    # So: refuse to grade on a derivation that collapsed. A gate count of zero
    # is not a repo with no gates, it is a broken reading of this directory,
    # and the difference matters because the second one prints four confident
    # WRONGs at correct documents.
    assert gates, ("found no gate scripts in cad/ -- the docstring convention "
                   "they are counted by must have changed; fix this derivation "
                   "before trusting anything below")

    # render.py's own tables, read by importing it rather than by parsing it.
    # Its views are 9 hand-aimed cameras plus a loop over the 6 print plates,
    # and only 4 of them get a light twin, so the total is not a number anyone
    # should be re-deriving in prose. Two documents said fifteen, which was
    # true until the light twins existed -- and I added those myself, in the
    # commit that put a light/dark pair in the README.
    spec = importlib.util.spec_from_file_location("_render",
                                                  os.path.join(cad, "render.py"))
    rmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rmod)

    return {"asserts": len(re.findall(r"\bassert\s*\(", body)),
            "gates": len(gates),
            "renders": len(rmod.VIEWS) + len(rmod.LIGHT_VIEWS),
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


# --------------------------------------------------------------------------
#  TABLE ARITHMETIC — the numbers the model cannot produce
#
#  Filament mass needs a slicer and prices need a vendor, so neither can be
#  derived from the geometry. They are still not typed here: they are SUMMED
#  from the repo's own tables, and then the headline figures are checked
#  against the rows they are supposed to total. That is the failure that
#  actually happened — the 674 g headline counts one ballast pod and one lid
#  where the table below it specifies two of each.
# --------------------------------------------------------------------------
def strip(x):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).strip()


def rows(html_seg):
    return [[strip(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
            for r in re.findall(r"<tr>(.*?)</tr>", html_seg, re.S)]


def table_facts():
    b = open(os.path.join(ROOT, "docs", "build.html"), errors="replace").read()

    seg = b[b.index("03 / Printed parts"):b.index("04 /", b.index("03 / Printed parts"))]
    mass = {"PETG": 0.0, "TPU": 0.0}
    for c in rows(seg):
        if len(c) < 6 or not re.fullmatch(r"\d+", c[1]) or c[2] not in mass:
            continue
        m = re.match(r"([\d.]+) ?g", c[5])
        if m:
            mass[c[2]] += int(c[1]) * float(m.group(1))

    i = b.index("What it costs", b.index("11 /") if "11 /" in b else 0)
    usd = 0.0
    for c in rows(b[i:i + 3000]):
        if len(c) == 3 and re.fullmatch(r"[\d.]+", c[2]) and "total" not in c[0].lower():
            usd += float(c[2])

    # Fasteners come from the per-joint table in assembly.md, which is the only
    # place they are enumerated rather than summarised.
    a = open(os.path.join(ROOT, "hardware", "assembly.md"), errors="replace").read()
    # Anchor on the header: the print-plate table above it is also four columns
    # ending in a digit, and summing that one silently gave 36 of everything.
    head = a.index("| Joint | Insert | Screw | Qty |")
    ins = scr = std = 0
    for line in a[head:].split("\n\n")[0].split("\n"):
        c = [x.strip() for x in line.strip("|").split("|")]
        if len(c) != 4 or not re.fullmatch(r"\d+", c[3]):
            continue
        q = int(c[3])
        if c[1] != "—":
            ins += q
        if "standoff" in c[2].lower():
            std += q
        elif c[2] != "—":
            scr += q
    return {"petg_g": mass["PETG"], "tpu_g": mass["TPU"],
            "filament_g": sum(mass.values()), "parts_usd": usd,
            "inserts": ins, "screws": scr, "standoffs": std}


def facts(quick=False):
    f = dict(echo_facts())
    f.update(source_facts())
    f.update(table_facts())
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
        # (?<![\w-]) so a hyphenated number word is not chopped: without it
        # "twenty-one asserts" captured "one" and the gate reported the
        # document wrong when the document was right. A gate that cries wolf
        # gets switched off.
        ("assert count",
         r"(?<![\w-])(\d+|[a-z]+(?:-[a-z]+)?) (?:in-model )?asserts?\b", eq("asserts")),
        ("printed-part count",
         r"(\d+|[a-z]+) printed parts\b", eq("parts")),
        # A shields.io badge reads "label-message-colour", so its number comes
        # after the words rather than before them, and the whole thing is
        # percent-encoded. Both badges in the README were already graded — but
        # only through their alt text, which is plain. Change the URL alone and
        # the badge renders "printed parts | 99" over an alt that still says
        # eleven, and this gate called the repo clean. See decoded_lines().
        ("printed-part badge",
         r"badge/printed parts-(\d+)", eq("parts")),
        ("parts in a heading",
         r"<h3>(\d+|[A-Za-z]+) parts, two materials", eq("parts")),
        ("plate count",
         r"(\d+|[a-z]+) plates\b", eq("plates")),
        # [A-Za-z] because the stale ones were sentence-initial: "Two gates,
        # because a render lies" sat directly above a block that runs five
        # commands. "mesh gates", "both gates" and "the gates" each capture a
        # word num() cannot parse, and scan() drops those as not-a-claim.
        ("gate count",
         r"(\d+|[A-Za-z]+) gates\b", eq("gates")),
        ("render count",
         r"(\d+|[a-z]+) images\b", eq("renders")),
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
        # These need a totalling context. "430 g of filament" is the shell,
        # "4 screws from below" is an access count, and "M2.5 standoffs" put a
        # 5 in front of the word — all three tripped a looser pattern.
        ("filament total",
         r"(?<![\d.])(\d+) ?g\**" + SEP + r"(?:of filament across|total\b)",
         eq("filament_g", 0.5)),
        ("filament, stat box",
         r"Filament<b>[^0-9]{0,40}?(\d+) ?g", eq("filament_g", 0.5)),
        ("PETG subtotal", r"(?<![\d.])(\d+) ?g PETG", eq("petg_g", 0.5)),
        ("TPU subtotal", r"(?<![\d.])(\d+) ?g TPU", eq("tpu_g", 0.5)),
        ("insert count",
         r"(?<![\d.])(\d+) (?:brass )?(?:heat-set )?inserts\b", eq("inserts")),
        # Only where it is stated as a whole-device total, i.e. beside the
        # insert count, never as "four screws from the front".
        ("screw count",
         r"\d+ (?:brass )?(?:heat-set )?inserts[^.]{0,12}?(?<![\d.])(\d+) screws\b",
         eq("screws")),
        ("standoff count",
         r"\d+ screws[^.]{0,6}?(?<![\d.])(\d+) standoffs\b", eq("standoffs")),
        ("parts total",
         r"(?:come to|no light bar\D{0,12}) ?≈? ?\*{0,2}\$(\d+)(?![\d.])",
         eq("parts_usd", 0.5)),
        ("parts total, restated",
         r"≈ ?\$(\d+) in parts", eq("parts_usd", 0.5)),
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
        checks = CHECKS(f)

        def claims(hay, lineno=None):
            for label, pat, (ok, want) in checks:
                for m in re.finditer(pat, hay):
                    got = m.group(1)
                    try:
                        good = ok(got)
                    except (KeyError, ValueError, TypeError):
                        continue          # unparseable capture, not a claim
                    if not good:
                        at = lineno or text.count("\n", 0, m.start()) + 1
                        yield (rel, at, label, " ".join(m.group(0).split()), want())

        bad += list(claims(flat))
        bad += list(decoded_lines(text, claims))
    return sorted(set(bad))


def decoded_lines(text, claims):
    """Grade the claims that only exist percent-encoded, i.e. inside a URL.

    A badge's visible number is in its query string, where "152 x 150 x 192"
    is spelled "152%20%C3%97%20150%20%C3%97%20192" and no pattern here can see
    it. Decoding is per line and only for lines that actually contain a '%',
    which across the ten gated documents is five lines, all of them in the
    README's header. Per line rather than whole-file because decoding shortens
    the text, so offsets into it no longer locate anything, and a gate that
    reports the wrong line number is most of the way to being ignored.
    """
    for i, line in enumerate(text.splitlines(), 1):
        if "%" not in line:
            continue
        decoded = unquote(line)
        if decoded != line:
            yield from claims(decoded, lineno=i)


def self_test(f):
    """Catch a wrong number that was planted, before reporting none were found.

    This was the only gate here without a control, and it is the one that most
    needed one. Every pattern in CHECKS is deliberately tight, because a loose
    one cries wolf -- but a pattern tightened until it no longer matches is
    indistinguishable, from the outside, from ten documents that all agree.
    Both print "every claim agrees with the model".

    Two things are planted. A plain claim, which exercises the patterns and the
    comparison, and a percent-encoded badge, which exercises the decode pass --
    the one that existed for an afternoon before anyone checked whether a URL
    it could not read was a URL with a wrong number in it.
    """
    want = f["gates"] + 1
    probe = (f"a document claiming {want} gates\n"
             f'<img src="https://img.shields.io/badge/printed%20parts-{f["parts"] + 1}-1D2226" />\n')

    # Through scan() on a real file, not by re-running the patterns here. The
    # first version of this control did the latter and it was theatre: delete
    # the decode pass from scan() entirely and the control still passed, because
    # it was never calling the code it claimed to be protecting. It only caught
    # a pattern that stopped matching, which is half the failure.
    path = os.path.join(tempfile.mkdtemp(), "planted.md")
    with open(path, "w") as g:
        g.write(probe)
    caught = {label for _rel, _line, label, _text, _want in scan(f, [path])}

    missed = {"gate count", "printed-part badge"} - caught
    if missed:
        print("  HARNESS BROKEN  planted a wrong number and this gate did not see it")
        for label in sorted(missed):
            print(f"                  {label} matched nothing it should have failed")
        print("  a clean report below would mean nothing — refusing to run")
        return False
    return True


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

    if not self_test(f):
        return 2

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
