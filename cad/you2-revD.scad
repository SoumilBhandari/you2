// ============================================================================
//  YOU2 — desk voice assistant.  ENCLOSURE rev D
//  OpenSCAD 2021.01+   ·  render PNGs with --render, never preview
//
//  DESIGN CONSTRAINTS THIS FILE IS BUILT AGAINST
//    · One ordinary FDM printer. 0.4 mm nozzle, 220 x 220 x 250 bed, one
//      material at a time. NO machined parts, NO laser cutting, and NO tapped
//      plastic anywhere — every thread is a brass heat-set insert, which needs
//      a soldering iron and nothing else.
//    · The shell prints TOP FACE DOWN with no support: both openings then face
//      up or steeply sideways and nothing is a horizontal ceiling.
//    · Mass is optional and free. Two sand-filled ballast pods use the dead
//      volume either side of the Pi — no machining, ~220 g, and it lands at
//      the very bottom where it fights sliding, which is the real failure mode.
//
//  WHY IT LOOKS LIKE THIS
//    Exposed socket-head fasteners in counterbores, fluted flanks, a shadow
//    gap above the base, one big round eye. An instrument, not an appliance —
//    and each of those is also structural: the flutes are ribs on the panel
//    most likely to be pushed, the counterbores keep every head below the
//    bezel face so nothing proud can catch, the shadow gap hides the seam.
//
//  Coordinates:  X = width (centred on 0)   Y = depth (front -Y)   Z = height
//                z = 0 is the underside of the base plate.
//
//  Every part dimension is still from a datasheet, not calipers — see
//  hardware/measure-first.md. The model asserts its own buildability, so a
//  wrong measurement fails loudly with a reason instead of rendering a shell
//  that cannot be assembled.
// ============================================================================

$fn = $preview ? 48 : 120;

// MODE: assembly | exploded | section | shell | faceplate | base | mic_clamp
//     | driver_clamp | ballast_pod | ballast_lid | mic_gasket | panel_gasket
//     | speaker_gasket
//     | foot | diffuser | printplate
MODE     = "exploded";
EXPLODE  = 62;
LED_BAR  = false;      // costs 14 mm of height and 8 mm of depth
FLUTES   = false;     // optional. Smooth reads cleaner; see the note on flutes()
BALLAST  = true;

/* [Printer envelope — asserted below] -------------------------------------- */
BED_X = 220; BED_Y = 220; BED_Z = 250;

/* [Overall form] ----------------------------------------------------------- */
// Both are hand-picked constants that the asserts GUARD rather than derive, and
// the handoff lists deriving them as work. It has not been done because the
// binding constraint is not the one the assert checks.
//
// The flange assert wants W > 151. At W = 152 the default variant builds and
// LED_BAR = true does not: the rebate's top-front corner pinches against the
// rolled corner and the shell exports with a 1.7 x 1.2 x 2.0 mm chip floating
// at each side. Swept with check.py, the real threshold for that variant is
// W = 154 — 152 and 153 chip, 154 and up are clean.
//
// So W's true lower bound moves with LED_BAR even though nothing in the
// x-direction does: fp_w and fp_lip_x are identical in both. What changes is
// fp_l, and through it H, D and the rake, which slides the rebate's corner
// along a surface that is rolled in plan AND filleted in profile. Deriving that
// means re-deriving the rolled corner, and a formula that is subtly wrong is
// worse than a constant a gate checks. 158 keeps 4 mm over the measured
// threshold. check.py --variants is the authority, not the assert.
W            = LED_BAR ? 158 : 152;
D            = LED_BAR ? 158 : 150;
wall         = 3.0;
face_wall    = 4.6;
rake         = 22;
cr_plan      = 5;      // was 8: the faceplate's side edges sat 2.2 mm out
                       // onto the plan roll, with no flat to land on
cr_prof      = 3;      // was 6: the profile fillets ate the top/bottom land

/* [Display — Waveshare 3.4" DSI round (C)] --------------------------------- */
disp_carrier = 115.0;  // square carrier PCB              <-- MEASURE
disp_active  = 87.6;   // visible round active area       <-- MEASURE
disp_panel_d = 91.5;   // round panel body OD             <-- MEASURE
disp_pcb_t   = 1.8;
disp_stack   = 6.5;
glass_recess = 2.2;
panel_gask   = 1.0;    // TPU ring under the panel flange
chamfer      = 0.8;    // step around the display aperture
aperture     = disp_active + 1.0;
disp_hole_p  = 105.0;  // carrier mounting-hole pitch     <-- MEASURE

/* [Faceplate + fasteners] --------------------------------------------------- */
fp_t         = 4.2;
fp_rebate    = 2.2;
fp_lip_x     = 11.0;   // wide enough to host a M3 socket head + 2 mm of edge
fp_lip_y     = 6.0;
// Corner radius of the faceplate and of the rebate it drops into. This is the
// one number in the file that is purely how it looks. At 6 the plate was a
// hard-cornered rectangle carrying a circle, with the shell's own rectangle
// outside it — three outlines arguing. Large enough and the plate reads as one
// soft shape around the eye instead. It is bounded above by the corner screws:
// the counterbore has to stay in material, and the assert below says by how
// much.
fp_r         = 16.0;
// HEAT-SET INSERTS — these are HOLE diameters, not insert outer diameters.
// A heat-set insert needs the hole SMALLER than its OD so the melt has
// something to grip; boring the hole at the OD gives zero interference and the
// insert spins out the first time you torque a screw.
ins_m3_d     = 3.6;    // hole for M3 SHORT  (insert 4.0 OD x 4.0 long)
ins_m3_l     = 4.4;    // depth
ins_m3_dd    = 4.0;    // hole for M3 STANDARD (insert 4.6 OD x 5.8 long)
ins_m3_ll    = 6.0;    // depth
ins_m25_d    = 3.2;    // hole for M2.5 (insert 3.5 OD x 5.0 long). The display
ins_m25_l    = 5.2;    // carrier and the Pi both have 2.5-2.7 mm holes — an M3
                       // screw does not pass through either of them.
boss_od      = 10.0;   // 2.2x the 4.6 mm insert OD. At 8.0 it was 1.74x.
post_od      = 7.6;
post_id      = ins_m25_d;

/* [Microphones — reSpeaker Flex XVF3800 Circular-4] ------------------------ */
mic_pcb_d    = 73.0;
mic_circle_d = 44.0;   // capsule PITCH CIRCLE            <-- MEASURE FIRST
mic_port_d   = 3.0;
mic_mem_d    = 6.2;    // GORE GAW3343.06.0 is OD 6.0 — 6.2 locates it
mic_mem_t    = 0.4;
mic_inset_y  = 25;     // DERIVED-ish: centres the array in the usable top face.
                       // At 14 it overhung the raked face's top edge by 2 mm.
mic_edge_keep = 4.0;   // ring of bare PCB left outboard of each membrane:
                       // the GORE part is adhesive-bonded, and 2 mm of rim is
                       // not a bond line. This is what the assert enforces.
mic_gasket_t = 1.5;
mic_squash   = 1.2;    // clamped height -> 0.3 mm (20 %) gasket compression
mic_clamp_t  = 3.0;
mic_boss_x   = 44;

/* [Speaker — enclosed rectangular driver] ---------------------------------- */
spk_bx       = 50;     // driver box width                <-- MEASURE
spk_by       = 45;     // driver box height               <-- MEASURE
spk_bz       = 22;     // driver box depth                <-- MEASURE
spk_cone_d   = 36;     // RADIATING diameter, not the box   <-- MEASURE
spk_grille_d = spk_cone_d + 2;   // holes outside the cone are blocked and do
                                 // nothing; +2 keeps one ring over the surround
grille_hole  = 2.8;    // 40.3 % open over the field
grille_pitch = 4.2;
seal_land    = 1.2;    // how far the seat stands proud of the skirt's inner face
seal_w       = 4.0;    // seat width outboard of the driver box
spk_seat     = 3.0;    // how far the seat overlaps the driver's front face
spk_gask     = 1.0;    // TPU gasket between driver and seat
spk_boss_x   = 7;

/* [Light bar — optional] ---------------------------------------------------- */
bar_len      = 102.0;
bar_w        = 10.4;
bar_slot_w   = 4.0;

/* [Raspberry Pi 5] ---------------------------------------------------------- */
// USB-A x4 and RJ45 are on a 56 mm SHORT edge; USB-C + micro-HDMI on an 85 mm
// LONG edge. The board is turned so the short edge faces the rear window.
pi_long      = 85;
pi_short     = 56;
pi_hole_long = 58;
pi_hole_shrt = 49;
pi_hole_off  = 10;     // hole-pattern centre is 10 mm from the BOARD centre
pi_y         = 8;
standoff_h   = 6;

/* [Base plate + ballast] ---------------------------------------------------- */
base_t       = 4.0;
base_clear   = 0.5;    // the plate is a true offset of the opening; 0.35 left a
                       // tessellation sliver at the rounded corners
// The drain field is rotated 90 degrees when it is cut, so this LENGTH runs
// along x and the pitch runs along y. At 44 it reached x = +-22.0 while the
// cable-tie slots start at x = +-22.4 — a 0.4 mm web between two voids, which
// is below what a 0.4 mm nozzle lays down, so the slicer merges them into one
// opening. Neither geometry gate can see it: both features are CUTS, and
// fit.py compares solids.
drain_n      = 5;
drain_pitch  = 17;
drain_len    = 40;
drain_w      = 5;
tie_x        = 24;     // first cable-tie slot centre; the second is +10
tie_w        = 3.2;
tie_l        = 9;
sd_slot_w    = 16;
sd_slot_l    = 10;     // narrow, and outboard of the Pi hole column at
                       // x = -24.5 — a wider window undercuts that standoff boss
// Pods sit OUTBOARD of the Pi's left-edge connectors. A right-angle USB-C plug
// protrudes ~11 mm past the board edge (x = -28), reaching x = -39, so the pod's
// inner face must clear -40; and the vertical side ribs occupy |x| = 68..74, so
// its outer face must clear 62. That is a 22 mm window, which is why the pods
// are tall rather than wide.
pod_w        = 22;
pod_l        = 96;     // clears the internal cable clamp at y 58..74
pod_h        = 40;
pod_wall     = 2.0;

/* [Foot — TPU shoe, optional] ----------------------------------------------- */
foot_t       = 3.0;
foot_wrap    = 5.0;    // was 9.0, which hid the shadow gap completely

/* [Rear I/O + venting] ------------------------------------------------------ */
io_w         = 64;
io_z0        = 9;
io_z1        = 37;
vent_w       = 84;
vent_slot_w  = 3.4;
vent_pitch   = 6.6;
vent_h       = 40;
inlet_d      = 14;    // rubber grommet. NOT a panel-mount USB-C coupler:
                      // that would add two contact interfaces to a 5 A rail
                      // feeding a board that is fussy about supply voltage.
                      // The official PSU's captive cable passes straight through.
inlet_x      = 44;
inlet_z      = 22;
saddle_tie_d  = 3.6;   // zip tie through the saddle's side walls
saddle_tie_dy = -3;    // from the saddle centre, FORWARD of the inlet bore

/* [Detailing] --------------------------------------------------------------- */
flute_d      = 5.0;
flute_depth  = 0.9;
flute_pitch  = 9.0;
gap_z        = 7.0;
gap_h        = 2.0;
gap_d        = 1.2;

// ============================================================================
//  DERIVED — the box is sized BY the parts, not the other way round
// ============================================================================
t_top0    = cr_prof * tan((90 - rake) / 2);
t_sh0     = cr_prof * tan(rake / 2);
bar_allow = LED_BAR ? bar_w + 6 : 0;

// Depth of the clamp face below the skirt's outer skin. The 0.8 is the 20 %
// gasket squash: the driver's own back face lands 0.2 mm further out than this,
// so bolting the clamp down compresses the gasket by exactly that difference.
drv_reach = wall + seal_land + spk_gask*0.8 + spk_bz;

spk_z     = base_t + 4 + seal_w + spk_by/2;
skirt_h   = ceil(spk_z + spk_by/2 + seal_w + 4);

fp_w      = disp_carrier + 4;
fp_l      = disp_carrier + bar_allow + 4;
lip_min   = 1.5;                       // flat shell OUTBOARD of the rebate
req_flat  = fp_l + 2*(fp_lip_y + lip_min);
H         = ceil(skirt_h + (req_flat + t_top0 + t_sh0) * cos(rake));

rise      = H - skirt_h;
setback   = rise * tan(rake);
slope     = rise / cos(rake);
t_top     = t_top0;
t_sh      = t_sh0;
flat      = slope - t_top - t_sh;
fmargin   = (flat - fp_l) / 2;
top_front = -D/2 + setback + t_top;
top_rear  =  D/2 - wall - 2;
top_avail = top_rear - top_front;

disp_s = t_sh + fmargin + bar_allow + 2 + disp_carrier/2;
bar_s  = t_sh + fmargin + 2 + bar_w/2;
fp_c   = t_sh + flat/2;
post_h = disp_stack + panel_gask - (fp_t - glass_recess);   // display standoff

echo(str("OVERALL           = ", W, " x ", D, " x ", H, " mm    LED_BAR=", LED_BAR));
echo(str("flat raked face   = ", flat, " mm, flange ", fmargin, " mm top/bottom"));
echo(str("top face for mics = ", top_avail, " mm  (needs ", mic_pcb_d + 4, ")"));
echo(str("skirt             = ", skirt_h, " mm, driver centre z = ", spk_z));
echo("VENTING IS REAR-ONLY. Needs >= 60 mm clearance behind the device.");

assert(fmargin - fp_lip_y > 1.2, "no printable lip outboard of the faceplate rebate — raise H or cut fp_lip_y");
assert((W - 2*wall - fp_w)/2 > fp_lip_x + 2,   "no side flange — raise W");
// the socket head must sit fully inside the flange with edge left over
assert(fp_lip_x > 6.2 + 4,                     "flange too narrow for an M3 socket head counterbore");
assert(top_avail > mic_pcb_d + 4,              "top face too shallow for the array — raise D or drop the rake");
assert(mic_inset_y - mic_pcb_d/2 > top_front + 2, "mic array overhangs the front edge of the top face");
assert(mic_inset_y + mic_pcb_d/2 < top_rear - 2,  "mic array overhangs the rear wall");
assert(spk_z - spk_by/2 - seal_w > base_t,     "driver box fouls the base plate");
assert(spk_z + spk_by/2 + seal_w < skirt_h,    "driver box breaks through the shoulder");
// NOT "the ports fall off the board" — at mic_circle_d = 62.2 the membrane's
// outer edge still sits 2.3 mm INSIDE a 73 mm PCB. What fails is the bond land.
assert(mic_circle_d/2 + mic_mem_d/2 + mic_edge_keep < mic_pcb_d/2,
       str("no bond land for the mic membranes — the ", mic_mem_d,
           " mm vent needs ", mic_edge_keep, " mm of PCB outboard of it and has ",
           mic_pcb_d/2 - (mic_circle_d/2 + mic_mem_d/2)));
assert(mic_boss_x + boss_od/2 < W/2 - wall - 2, "mic clamp bosses foul the side wall");
// The bore must house the insert AND stop short of the outside of the top face.
assert(mic_bore > 4.0 + 0.3,
       str("mic insert bore is ", mic_bore, " mm — too shallow for a 4.0 mm M3 insert"));
assert(mic_roof >= 1.2,
       "less than 3 perimeters of roof over the mic insert bore — it will break through");
// Two CUTS that nearly meet leave a web no nozzle can fill, and the slicer
// merges them into one opening. Neither mesh gate sees this: both are voids.
tie_web = (tie_x - tie_w/2) - drain_len/2;
assert(tie_web > 1.2,
       str("only ", tie_web, " mm of base between the drain slots and the ",
           "cable-tie slots — shorten drain_len or move tie_x"));
// Same failure one layer up: the saddle's tie hole and the inlet bore are both
// cuts, and they crossed. Keep the tie hole forward of where the bore starts.
saddle_tie_gap = (D/2 - (wall + 3)) - ((D/2 - 9) + saddle_tie_dy + saddle_tie_d/2);
assert(saddle_tie_gap > 1.2,
       str("saddle tie hole is ", saddle_tie_gap,
           " mm from the inlet bore — the two cuts will meet"));
// mic_squash is the CLAMPED height, so the compression is the difference.
mic_comp = mic_gasket_t - mic_squash;
assert(mic_comp > 0.05*mic_gasket_t && mic_comp < 0.45*mic_gasket_t,
       "mic gasket is uncompressed or crushed — check mic_squash against mic_gasket_t");
// The faceplate's own corner radius against its corner screws. The plate is
// fp_w + 2*(fp_lip_x-0.7) by fp_l + 2*(fp_lip_y-0.7); a screw at fp_screws()
// sits in the corner quadrant once fp_r is large, and its Ø6.2 counterbore has
// to stay inside the arc. This is the only thing stopping fp_r going higher.
fp_ow  = fp_w + 2*(fp_lip_x - 0.7);
fp_ol  = fp_l + 2*(fp_lip_y - 0.7);
fp_sx  = fp_w/2 + fp_lip_x/2;          // fp_screws(), first component
fp_sy  = fp_l/2 - 8;
fp_cx  = max(0, fp_sx - (fp_ow/2 - fp_r));
fp_cy  = max(0, fp_sy - (fp_ol/2 - fp_r));
fp_edge = fp_r - (sqrt(fp_cx*fp_cx + fp_cy*fp_cy) + 6.2/2);
// 1.2 mm is three perimeters at a 0.4 nozzle. Two is not enough on a corner
// carrying screw preload, and 18.0 — which the first cut of this used — leaves
// exactly two.
assert(fp_r < 6.05 || fp_edge > 1.2,
       str("faceplate corner radius eats the screw counterbore — fp_r = ", fp_r,
           " leaves ", fp_edge, " mm of material outboard of it, needs 1.2"));

assert(max(W, D) <= max(BED_X, BED_Y) && min(W, D) <= min(BED_X, BED_Y),
       "shell footprint exceeds the bed");
assert(H <= BED_Z,                             "shell is taller than the build volume");
// Stack-ups must actually CLOSE onto their gaskets. Both of these were wrong by
// less than a millimetre — exactly the kind of error a render cannot show and
// an interference check cannot see, because nothing overlaps: it just floats.
disp_squash = panel_gask - (post_h - (disp_stack - disp_pcb_t));
assert(disp_squash > 0.05*panel_gask && disp_squash < 0.45*panel_gask,
       "display posts wrong length — the panel gasket is uncompressed or crushed");
// The driver gasket gets NO squash assert, deliberately. The clamp face is
// DEFINED as drv_reach = ... + spk_gask*0.8 + ..., so the compression is 20 % by
// construction and any assert on it is a tautology dressed as a check. The one
// that was here — `spk_gask*0.2 > 0.1` — is the shape that mistake takes: true
// for every gasket over 0.5 mm, containing no term from the clamp at all. The
// display and mic gaskets DO get asserts because their stack-ups are built from
// independent numbers that can disagree; this one cannot.
//
// What is not definitional about the driver mount is the two things below.
assert(spk_boss_x - 5 > 1,
       "driver clamp webs foul the driver box — the 10 mm web needs 1 mm clear of spk_bx/2");
assert(ins_m3_ll + 1 < drv_reach - wall - seal_land,
       "M3 insert is longer than the web is deep — it would break into the driver pocket");

// ============================================================================
//  HELPERS
// ============================================================================
module rrect(w, d, r) { offset(r = r) offset(delta = -r) square([w, d], center = true); }
module side_profile(shrink = 0) {
    offset(delta = -shrink)
    offset(r = cr_prof) offset(delta = -cr_prof)
        polygon([[-D/2, 0], [D/2, 0], [D/2, H], [-D/2 + setback, H], [-D/2, skirt_h]]);
}
module plan_mask(w, d, h) { linear_extrude(height = h, center = true) rrect(w, d, cr_plan); }
module body(shrink = 0) {
    intersection() {
        rotate([90, 0, 90]) linear_extrude(height = W - 2*shrink, center = true) side_profile(shrink);
        plan_mask(W - 2*shrink, D - 2*shrink, H*3);
    }
}
// Internal features are intersected with body(shrink = INNER), a solid slightly
// LARGER than the cavity, so they overlap the wall and fuse to it. Using
// wall + eps makes them float free and the shell exports in pieces — that was
// rev C's defect and cad/check.py exists to catch it.
INNER = wall - 0.8;

module on_face(s) {
    translate([0, -D/2 + s*sin(rake), skirt_h + s*cos(rake)]) rotate([90 - rake, 0, 0]) children();
}
module on_skirt(z) { translate([0, -D/2, z]) rotate([90, 0, 0]) children(); }
module on_rear(z)  { translate([0,  D/2, z]) rotate([-90, 0, 0]) children(); }

module hexgrille(field_d, hole_d, pitch, t) {
    intersection() {
        cylinder(d = field_d, h = t, center = true);
        for (r = [-12:12], c = [-12:12])
            translate([(c + (r % 2 == 0 ? 0 : 0.5)) * pitch, r * pitch * 0.866, 0])
                cylinder(d = hole_d, h = t + 1, center = true);
    }
}
module slot_field(n, pitch, len, w, t) {
    for (i = [-floor(n/2) : floor(n/2)])
        translate([i * pitch, 0, 0]) hull() {
            translate([0,  len/2 - w/2, 0]) cylinder(d = w, h = t);
            translate([0, -len/2 + w/2, 0]) cylinder(d = w, h = t);
        }
}

module fp_outline(gx = 0, gy = 0) { rrect(fp_w + 2*gx, fp_l + 2*gy, fp_r); }
function fp_screws() = [for (sx = [-1,1], sy = [-1,1])
                        [sx*(fp_w/2 + fp_lip_x/2), sy*(fp_l/2 - 8)]];
function post_xy()   = [for (sx = [-1,1], sy = [-1,1]) [sx*disp_hole_p/2, sy*disp_hole_p/2]];
function base_xy()   = [for (sx = [-1,1], sy = [-1,1])
                        [sx*(W/2 - wall - 5), sy*(D/2 - wall - 5)]];
function pod_x()     = [for (sx = [-1,1]) sx*53];

// ============================================================================
//  SHELL
// ============================================================================
module face_opening() {
    on_face(fp_c) {
        translate([0, 0, -(face_wall + 40)]) linear_extrude(height = face_wall + 41) fp_outline(0, 0);
        translate([0, 0, -fp_rebate])        linear_extrude(height = fp_rebate + 6)  fp_outline(fp_lip_x, fp_lip_y);
    }
}
module face_shelf() {
    intersection() {
        body(shrink = INNER);
        on_face(fp_c) translate([0, 0, -face_wall])
            linear_extrude(height = face_wall - wall + 0.01) fp_outline(fp_lip_x + 6, fp_lip_y + 6);
    }
}
module face_bosses() {
    intersection() {
        body(shrink = 0.01);
        on_face(fp_c) for (p = fp_screws())
            translate([p[0], p[1], -(face_wall + 9)]) cylinder(d = boss_od, h = 11);
    }
}
module face_boss_holes() {
    on_face(fp_c) for (p = fp_screws())
        translate([p[0], p[1], -(face_wall + ins_m3_ll + 1)])
            cylinder(d = ins_m3_dd, h = ins_m3_ll + 1 + face_wall);
}

module mic_cut() {
    translate([0, mic_inset_y, H]) for (i = [0:3]) rotate([0, 0, i*90 + 45])
        translate([mic_circle_d/2, 0, 0]) {
            translate([0, 0, -wall - 2]) cylinder(d = mic_port_d, h = wall + 4);
            // outer counterbore: an ePTFE acoustic membrane bonds in here. It
            // is what makes the top face spill- and dust-proof without closing
            // the acoustic path.
            translate([0, 0, -mic_mem_t]) cylinder(d = mic_mem_d, h = mic_mem_t + 2);
        }
}
// The array is pulled UP onto its gasket by a ring on two screws that sit
// BESIDE the PCB. Nothing is tapped, nothing passes through the board, and the
// bosses bottom out so the gasket cannot be crushed.
mic_stack = mic_squash + 1.4 + mic_clamp_t;
// The boss must stop where the CLAMP starts, not where the screw head does —
// run it the full stack height and it occupies the same space as the ring.
mic_under = mic_squash + 1.4;          // gasket (compressed) + PCB
//: Material left above the mic insert bore. The bore used to ask for
//: ins_m3_l + 1.0 = 6.2 mm out of the 5.6 mm that exists between the clamp face
//: and the outside of the top face, so it came OUT THE TOP: two 3.6 mm holes in
//: the one surface this design seals, straight into the cavity, on a box whose
//: acoustic rule is that nothing vents through the top. A render does not show
//: it and no assert looked.
mic_roof  = 1.2;                       // 3 perimeters at a 0.4 nozzle
mic_bore  = wall + mic_under - mic_roof;
module mic_bosses() {
    intersection() {
        body(shrink = INNER);
        for (sx = [-1,1]) translate([sx*mic_boss_x, mic_inset_y, H - wall - mic_under])
            cylinder(d = boss_od, h = mic_under + 0.01);
    }
}
module mic_boss_holes() {
    // Depth is derived from what is THERE, not from what the insert would like.
    // The M3 x 6 cannot bottom out regardless: it passes through a 3.0 mm clamp
    // ring and so engages 3.0 mm of a 4.0 mm insert, leaving its tip 1.0 mm
    // clear of the insert's own bottom.
    for (sx = [-1,1]) translate([sx*mic_boss_x, mic_inset_y, H - wall - mic_under - 0.1])
        cylinder(d = ins_m3_d, h = mic_bore + 0.1);
}

module speaker_cut() {
    on_skirt(spk_z) translate([0, 0, -wall/2]) hexgrille(spk_grille_d, grille_hole, grille_pitch, wall + 6);
}
// The pocket must start BEHIND the seat and its gasket. Cutting it from the
// wall inward — as it was — removes the seat's inner lip, so the opening ends
// up larger than the driver, the driver passes straight through without
// touching anything, and a 0.5 mm slot connects the driver's front chamber to
// the cavity the microphones sit on.
// NOTE: an earlier revision had a rectangular locating collar here. It was
// 2.2 mm clear of the skirt — anchored only through the driver webs — and its
// top rail was a 54 mm unsupported bridge in the print orientation. The driver
// is located by the seat it presses against and by the two webs either side of
// it (10 mm thick at x = +-32, against a 50 mm box), and held by the clamp.
// One fewer feature, and one fewer thing to print in mid-air.

module speaker_seal_land() {
    intersection() {
        body(shrink = INNER);
        on_skirt(spk_z) translate([0, 0, -wall - seal_land]) linear_extrude(height = seal_land + 0.01)
            difference() {
                square([spk_bx + 2*seal_w, spk_by + 2*seal_w], center = true);
                square([spk_bx - 2*spk_seat, spk_by - 2*spk_seat], center = true);
            }
    }
}
// The clamp anchors 27 mm behind the skirt. A cylinder spanning that gap is a
// HORIZONTAL cantilever: printed top-face-down its first layer is a 24 mm strip
// attached to the wall at one end only, needing support towers built from the
// bare bed inside a closed cavity. A web with a 45-degree ramp on its model-top
// edge spans the same gap, is rooted to the skirt over its whole height, and
// overhangs nothing. It also stiffens the panel the driver bolts to.
module driver_bosses() {
    intersection() {
        body(shrink = INNER);
        for (sx = [-1,1]) translate([sx*(spk_bx/2 + spk_boss_x), 0, 0])
            rotate([90, 0, 90]) linear_extrude(height = 10, center = true)
                polygon([[-D/2,             spk_z - 7],
                         [-D/2 + drv_reach, spk_z - 7],
                         [-D/2 + drv_reach, spk_z + 3],
                         [-D/2,             spk_z + 3 + drv_reach]]);
    }
}
module driver_boss_holes() {
    on_skirt(spk_z) for (sx = [-1,1])
        translate([sx*(spk_bx/2 + spk_boss_x), 0, -drv_reach - 0.1])
            cylinder(d = ins_m3_dd, h = ins_m3_ll + 0.2);
}

module rear_cut() {
    // on_rear maps local +Z to global +Y, so a cut must START at -(wall+margin)
    // or it leaves a membrane on the inside of the wall.
    for (z = [70, H - 41])
        on_rear(z) translate([0, 0, -(wall + 3)])
            slot_field(floor(vent_w/vent_pitch), vent_pitch, vent_h, vent_slot_w, wall + 6);
    on_rear((io_z0 + io_z1)/2) translate([0, 0, -(wall + 3)])
        linear_extrude(height = wall + 6) rrect(io_w, io_z1 - io_z0, 3);
    on_rear(inlet_z) translate([inlet_x, 0, -(wall + 3)]) cylinder(d = inlet_d, h = wall + 6);
}
module bar_cut() {
    if (LED_BAR) on_face(bar_s) {
        translate([0, 0, -face_wall/2]) cube([bar_len, bar_slot_w, face_wall + 4], center = true);
        translate([0, 0, -(face_wall + 1.2)]) cube([bar_len + 2, bar_w, 2.4], center = true);
    }
}

// Structure that is also the look. Vertical flutes stiffen a 148 mm flank,
// hide layer lines, and printed top-face-down each one is a constant
// cross-section, so they cost nothing in print quality or time.
module flutes() {
    // Optional. Confined to the REAR half of each flank and stopped clear of
    // the top face: run them forward and they notch the raked face's side edge
    // where the profile sets back, which reads as a defect rather than a
    // texture. Kept because they stiffen the flank and hide layer lines, but
    // the smooth shell is the better-looking default.
    if (FLUTES) for (sx = [-1,1])
        for (y = [-2 : flute_pitch : D/2 - 20])
            translate([sx*(W/2 + flute_d/2 - flute_depth), y, gap_z + gap_h])
                cylinder(d = flute_d, h = H - (gap_z + gap_h) - 10, $fn = 32);
}
// A shadow gap above the base seam, so the joint reads as a reveal.
module shadow_gap() {
    difference() {
        translate([0, 0, gap_z]) plan_mask(W + 2, D + 2, gap_h);
        translate([0, 0, gap_z]) plan_mask(W - 2*gap_d, D - 2*gap_d, gap_h + 2);
    }
}

module ribs() {
    intersection() {
        body(shrink = INNER);
        union() {
            // start ABOVE the base plate — a rib that runs to z = 0 occupies
            // the plate's own space, and the bottom opening is cut before the
            // ribs are unioned on, so nothing else catches it
            for (sx = [-1, 1], yy = [4, 30, 56])
                translate([sx * (W/2 - wall - 2), yy, base_t + (H - base_t)/2])
                    cube([6, 3.0, H - base_t], center = true);
            // Rear-wall stiffeners run VERTICALLY. Horizontal hoop ribs were
            // 32 cm2 of unsupported downward-facing ledge once the shell is
            // flipped for printing; a rib along z is a constant cross-section
            // in the print axis and overhangs nothing at all.
            for (sx = [-1, 1]) translate([sx*50, D/2 - wall - 3, (40 + H - 14)/2])
                cube([3.0, 8, H - 54], center = true);
        }
    }
}
module top_ribs() {
    intersection() {
        body(shrink = INNER);
        translate([0, 0, H - wall - 2.5]) union() {
            for (sx = [-1, 1]) translate([sx * 56, 0, 0]) cube([3.0, D, 6.0], center = true);   // clear of the clamp arms at +-52
            translate([0, mic_inset_y + mic_pcb_d/2 + 8, 0]) cube([W, 3.0, 6.0], center = true);
            translate([0, mic_inset_y - mic_pcb_d/2 - 6, 0]) cube([W, 3.0, 6.0], center = true);
        }
    }
}
module base_bosses() {
    intersection() {
        body(shrink = 0.01);
        // 45-degree cone on top: printed upside-down this face is the boss's
        // roof, and a flat 12 mm disc in mid-air droops.
        for (p = base_xy()) translate([p[0], p[1], base_t]) {
            cylinder(d = boss_od + 4, h = 11);
            translate([0, 0, 11]) cylinder(d1 = boss_od + 4, d2 = 0.6, h = 5.8);
        }
    }
}
module base_boss_holes() {
    for (p = base_xy()) translate([p[0], p[1], base_t - 0.1]) cylinder(d = ins_m3_dd, h = ins_m3_ll + 0.2);
}
// The mains cable is clamped to the shell, not to the Pi's surface-mount jack.
module cable_clamp() {
    intersection() {
        body(shrink = INNER);
        // A saddle open at the top, not a closed tube: the PSU's plug is
        // moulded on and is larger than any bore that would grip the jacket,
        // so the cable has to drop in sideways and be closed over with a tie.
        translate([inlet_x, D/2 - 9, inlet_z]) difference() {
            cube([17, 16, 20], center = true);
            cube([10.5, 26, 13], center = true);
            translate([0, 0, -8]) cube([10.5, 26, 12], center = true);   // opens DOWNWARD in the model = upward in print
            // ONE tie hole, through the two side walls only — full-width
            // slots cut the saddle into three disconnected pieces.
            //
            // There were two, at y = +-5 from the saddle centre. The rear one
            // landed at y = 71, inside the inlet bore's y 69..78, so the two
            // CUTS crossed and left 0.31 mm of material at the crossing —
            // found by the wall gate, invisible to the other two because
            // neither compares voids. Only 11 mm of saddle sits clear of the
            // bore; two holes fit in that with 1.2 mm webs and nothing to
            // spare, and one is enough: the hole opens through BOTH side
            // walls, so a tie goes in one side, over the jacket, and out the
            // other.
            translate([0, saddle_tie_dy, 5]) rotate([0, 90, 0])
                cylinder(d = saddle_tie_d, h = 30, center = true);
        }
    }
}

module shell() {
    difference() {
        union() {
            difference() {
                body();
                body(shrink = wall);
                translate([0, 0, base_t/2]) plan_mask(W - 2*wall, D - 2*wall, base_t + 2);
            }
            face_shelf(); speaker_seal_land(); driver_bosses(); mic_bosses();
            ribs(); top_ribs(); face_bosses(); base_bosses(); cable_clamp();
        }
        face_opening(); mic_cut(); speaker_cut(); rear_cut(); bar_cut();
        face_boss_holes(); base_boss_holes(); driver_boss_holes(); mic_boss_holes();
        flutes(); shadow_gap();
    }
}

// ============================================================================
//  FACEPLATE — local z = 0 at its BACK, z = fp_t at its FRONT.
//  Placed at on_face(fp_c) translate([0,0,-fp_rebate]).
// ============================================================================
module faceplate() {
    difference() {
        union() {
            linear_extrude(height = fp_t) fp_outline(fp_lip_x - 0.7, fp_lip_y - 0.7);
            // Post height is a STANDOFF, not the panel-body thickness. At
            // disp_stack - disp_pcb_t the 1.0 mm gasket had 0.19 mm to live in.
            for (p = post_xy()) translate([p[0], p[1] + (disp_s - fp_c), -post_h])
                cylinder(d = post_od, h = post_h);
        }
        translate([0, disp_s - fp_c, 0]) {
            // The bezel overlaps the panel border by (disp_panel_d-aperture)/2.
            // That overlap IS the retention: if the panel measures smaller than
            // assumed it goes to zero and the display falls out.
            translate([0, 0, fp_t - glass_recess - 0.01]) cylinder(d = aperture, h = glass_recess + 1);
            // A single crisp step, not a 45-deg cone: printed face-down a cone
            // comes out as a stack of concentric terraces, whereas one step is
            // one clean circle and reads as a machined counterbore.
            translate([0, 0, fp_t - chamfer]) cylinder(d = aperture + 2.8, h = chamfer + 0.01);
            translate([0, 0, -0.01]) cylinder(d = disp_panel_d + 1.2, h = fp_t - glass_recess + 0.02);
        }
        for (p = post_xy()) translate([p[0], p[1] + (disp_s - fp_c), -post_h - 0.1])
            cylinder(d = post_id, h = ins_m25_l + 0.2);
        for (p = fp_screws()) translate([p[0], p[1], -1]) {
            cylinder(d = 3.4, h = fp_t + 2);
            translate([0, 0, fp_t - 3.4]) cylinder(d = 6.2, h = 4.4);   // ISO 4762 head is 3.0 mm tall
        }
        if (LED_BAR) translate([-bar_len/2, bar_s - fp_c - bar_slot_w/2, -1])
            cube([bar_len, bar_slot_w, fp_t + 2]);   // center=true stopped 2.1 mm short of the face
    }
}

// ============================================================================
//  CLAMPS, BASE, BALLAST, GASKETS, FOOT
// ============================================================================
module mic_clamp_ring() {
    difference() {
        union() {
            difference() {
                cylinder(d = mic_pcb_d + 3, h = mic_clamp_t);
                translate([0, 0, -0.1]) cylinder(d = mic_pcb_d - 12, h = mic_clamp_t + 0.2);
            }
            // arms must extend PAST their screws — at cube([mic_boss_x,...])
            // the Ø3.4 hole at x = mic_boss_x fell half off the end
            for (sx = [-1,1]) translate([sx*(mic_boss_x + 8)/2, 0, mic_clamp_t/2])
                cube([mic_boss_x + 8, 14, mic_clamp_t], center = true);
            // Pressers directly over the four capsules, spoked back to the ring.
            // Without these the clamp squeezes the gasket 8.5 mm outboard of
            // every port and the seal never happens where it matters.
            for (i = [0:3]) rotate([0, 0, i*90 + 45]) hull() {
                translate([mic_circle_d/2, 0, 0]) cylinder(d = 9, h = mic_clamp_t);
                translate([(mic_pcb_d - 12)/2 + 1, 0, 0]) cylinder(d = 5, h = mic_clamp_t);
            }
        }
        for (sx = [-1,1]) translate([sx*mic_boss_x, 0, -0.1]) cylinder(d = 3.4, h = mic_clamp_t + 0.2);
    }
}
module driver_clamp() {
    difference() {
        union() {
            translate([0, 0, 1.75]) cube([spk_bx + 2*spk_boss_x + 14, 16, 3.5], center = true);
            for (sx = [-1,1]) translate([sx*(spk_bx/2 + spk_boss_x), 0, 0]) cylinder(d = 14, h = 3.5);
        }
        for (sx = [-1,1]) translate([sx*(spk_bx/2 + spk_boss_x), 0, -0.1]) cylinder(d = 3.4, h = 4);
    }
}

// A true inward offset of the shell's bottom opening. Using a different corner
// radius makes the plate's corners square into the shell's rounded ones.
module base_outline(grow = 0) {
    offset(delta = -base_clear + grow) rrect(W - 2*wall, D - 2*wall, cr_plan);
}
module base() {
    difference() {
        union() {
            linear_extrude(height = base_t) base_outline();
            translate([0, pi_y - pi_hole_off, base_t]) for (sx = [-1,1], sy = [-1,1])
                translate([sx*pi_hole_shrt/2, sy*pi_hole_long/2, 0]) cylinder(d = boss_od, h = 6);
            if (BALLAST) for (px = pod_x()) translate([px, pi_y, base_t])
                for (sy = [-1,1]) translate([0, sy*(pod_l/2 - 9), 0]) cylinder(d = boss_od, h = 5);
        }
        translate([0, pi_y - pi_hole_off, base_t + 6 - ins_m25_l]) for (sx = [-1,1], sy = [-1,1])
            translate([sx*pi_hole_shrt/2, sy*pi_hole_long/2, 0]) cylinder(d = ins_m25_d, h = ins_m25_l + 1);
        if (BALLAST) for (px = pod_x()) translate([px, pi_y, base_t + 5 - ins_m3_l])
            for (sy = [-1,1]) translate([0, sy*(pod_l/2 - 9), 0]) cylinder(d = ins_m3_d, h = ins_m3_l + 1);
        for (p = base_xy()) translate([p[0], p[1], -1]) {
            cylinder(d = 3.4, h = base_t + 2);
            // 90 deg included angle, mouth at the actual face — d1=7.0 from
            // z=-1 gave a 78.6 deg cone whose mouth was under-size for the head
            translate([0, 0, 1]) cylinder(d1 = 7.8, d2 = 3.4, h = 2.2);
        }
        // drain / debris path. NOT a thermal intake: buoyancy at a 3 mm foot
        // height cannot drive useful flow through a base at this scale.
        translate([0, pi_y, -1]) rotate([0,0,90])
            slot_field(drain_n, drain_pitch, drain_len, drain_w, base_t + 2);
        // the microSD socket is on the underside of the LONG edge that carries
        // USB-C and the micro-HDMIs — which this layout puts at x = -28, not +28
        translate([-35, pi_y - pi_long/2 + 8, -1]) linear_extrude(height = base_t + 2) rrect(sd_slot_l, sd_slot_w, 3);
        // cable-tie slots for the reSpeaker core board. "Tie it to a rib" was
        // not a feature that existed; this is.
        for (sx = [-1, 1], i = [0, 1])
            translate([sx*(tie_x + i*10), pi_y + pi_long/2 - 4, -1])
                linear_extrude(height = base_t + 2) rrect(tie_w, tie_l, 1.2);
    }
}

// Optional, and the reason no machined ballast is needed. Fill with dry sand
// (~110 g each) or steel shot (~300 g each), cap, screw down. Uses volume that
// is otherwise dead, costs no height, and puts mass at the very bottom.
module ballast_pod() {
    difference() {
        union() {
            linear_extrude(height = pod_h) rrect(pod_w, pod_l, 4);
            for (sy = [-1,1]) translate([0, sy*(pod_l/2 - 9), 0]) cylinder(d = 12, h = 5.5);
        }
        translate([0, 0, pod_wall]) linear_extrude(height = pod_h) rrect(pod_w - 2*pod_wall, pod_l - 2*pod_wall, 3);
        // counterbored from ABOVE. A countersink on the underside has nothing
        // to sink into — that face mates flat against the base's boss.
        for (sy = [-1,1]) translate([0, sy*(pod_l/2 - 9), -0.1]) {
            cylinder(d = 3.4, h = 10);
            translate([0, 0, 1.6]) cylinder(d = 6.6, h = 6);
        }
    }
}
// Light interference fit — at -0.4 it was a loose drop-in with nothing holding
// it. Press it home and run a bead of superglue round the seam; there is no
// room for a fastener on a 22 mm pod.
module ballast_lid() {
    linear_extrude(height = 2.0) rrect(pod_w - 2*pod_wall - 0.1, pod_l - 2*pod_wall - 0.1, 3);
}

// TPU frame between the driver's front face and its seat. Without it the
// driver is hard-mounted to the shell the microphones bolt to.
module speaker_gasket() {
    color("#3d4249", .96) linear_extrude(height = spk_gask)
        difference() {
            square([spk_bx + 2*seal_w - 2, spk_by + 2*seal_w - 2], center = true);
            square([spk_bx - 2*spk_seat, spk_by - 2*spk_seat], center = true);
        }
}
module mic_gasket() {
    color("#3d4249", .96) difference() {
        cylinder(d = mic_pcb_d, h = mic_gasket_t);
        for (i = [0:3]) rotate([0, 0, i*90 + 45])
            translate([mic_circle_d/2, 0, -0.5]) cylinder(d = mic_port_d + 0.4, h = 3);
    }
}
module panel_gasket() {
    color("#4a4f56", .95) difference() {
        cylinder(d = disp_panel_d + 1.0, h = panel_gask);
        translate([0, 0, -0.5]) cylinder(d = aperture - 1.0, h = 2);
    }
}
module foot() {
    color("#2c3136", .97) difference() {
        union() {
            linear_extrude(height = foot_t + foot_wrap) rrect(W + 2*foot_t, D + 2*foot_t, cr_plan + foot_t);
            // concentric with the plan corner radii, or each corner grows a
            // lopsided blob that breaks the silhouette in every view
            for (sx = [-1,1], sy = [-1,1])
                translate([sx*(W/2 - cr_plan), sy*(D/2 - cr_plan), 0]) cylinder(d = 2*(cr_plan + foot_t), h = foot_t + foot_wrap);
        }
        translate([0, 0, foot_t]) linear_extrude(height = foot_wrap + 1) rrect(W + 0.5, D + 0.5, cr_plan);
        for (sx = [-1,1], sy = [-1,1]) translate([sx*(W/2 - 18), sy*(D/2 - 18), -1])
            linear_extrude(height = foot_t + 2) rrect(22, 22, 4);
        // reach the corner windows, or the base's drain slots open into a
        // plenum with no outlet at all
        translate([0, 0, -1]) linear_extrude(height = foot_t + 2) rrect(W - 40, D - 40, cr_plan);
    }
}
module diffuser() {
    if (LED_BAR) color("#ffd28a", .9) on_face(bar_s)
        translate([0, 0, -face_wall/2]) cube([bar_len, bar_slot_w - 0.3, face_wall + 1.5], center = true);
}

// ============================================================================
//  PROXIES — fit check only
// ============================================================================
module proxy_pi() {
    translate([0, pi_y, base_t + 6 + standoff_h]) {
        color("#1f6b34", .95) linear_extrude(height = 1.6) square([pi_short, pi_long], center = true);
        color("#b9bdc1") for (x = [-22, -5]) translate([x, pi_long/2 - 17, 1.6]) cube([15, 17, 15.6]);
        color("#b9bdc1") translate([12, pi_long/2 - 17, 1.6]) cube([16, 17, 13.5]);
        color("#3d4145") translate([-pi_short/2 - 1, -30, 1.6]) cube([8, 9, 3.2]);
        color("#3d4145") for (y = [-14, 3]) translate([-pi_short/2 - 1, y, 1.6]) cube([7, 8, 3]);
        color("#c9a227") translate([-3, -25, 1.6]) cube([5, 51, 8.5]);
    }
}
module proxy_display() {
    on_face(disp_s) {
        color("#0a0d11") translate([0,0,-(disp_stack - disp_pcb_t)]) cylinder(d = disp_panel_d, h = disp_stack - disp_pcb_t - 0.9);
        color("#12361f", .97) translate([0,0,-disp_stack]) linear_extrude(height = disp_pcb_t) square([disp_carrier, disp_carrier], center = true);
        color("#3aa9de") translate([0,0,-0.95]) cylinder(d = disp_active, h = 1.0);
    }
}
module proxy_mics() {
    color("#7e57c2", .9) translate([0, mic_inset_y, H - wall - mic_squash - 1.4]) cylinder(d = mic_pcb_d, h = 1.4);
}
module proxy_driver() {
    color("#26292d", .97) on_skirt(spk_z)
        translate([-spk_bx/2, -spk_by/2, -wall - seal_land - spk_gask*0.8 - spk_bz])
            cube([spk_bx, spk_by, spk_bz]);
}
module proxy_inlet() { color("#4a4f56") on_rear(inlet_z) translate([inlet_x, 0, -4]) cylinder(d = inlet_d + 3, h = 6); }
module proxy_standoffs() {
    color("#b8952e") translate([0, pi_y - pi_hole_off, base_t + 6]) for (sx = [-1,1], sy = [-1,1])
        translate([sx*pi_hole_shrt/2, sy*pi_hole_long/2, 0]) cylinder(d = 5, h = standoff_h);
}
module proxy_bar() {
    if (LED_BAR) color("#ffb300", .95) on_face(bar_s) translate([0,0,-(face_wall + 0.4)]) cube([bar_len, bar_w, 1.6], center = true);
}
module ballast_placed() {
    // the pods sit ON their bosses, not over them
    if (BALLAST) for (px = pod_x()) translate([px, pi_y, base_t + 5]) color("#5d646b", .95) ballast_pod();
}

// ============================================================================
//  ASSEMBLY
// ============================================================================
FN = [0, -cos(rake), sin(rake)];

module assembly(e = 0) {
    shell();
    translate(FN * e * 2.15) on_face(fp_c) translate([0, 0, -fp_rebate]) faceplate();
    translate(FN * e * 1.60) proxy_display();
    translate(FN * e * 1.30) on_face(disp_s) translate([0, 0, -(fp_t - glass_recess) - panel_gask + fp_rebate]) panel_gasket();
    translate(FN * e * 2.60) proxy_bar();
    translate(FN * e * 2.85) diffuser();
    translate([0, 0,  e * 1.35]) translate([0, mic_inset_y, H - wall - mic_gasket_t]) mic_gasket();
    translate([0, 0,  e * 1.05]) proxy_mics();
    translate([0, 0,  e * 0.72]) translate([0, mic_inset_y, H - wall - mic_stack]) mic_clamp_ring();
    translate([0, -e * 1.20, 0]) proxy_driver();
    translate([0, -e * 1.50, 0]) on_skirt(spk_z) translate([0, 0, -(wall + seal_land)]) rotate([180,0,0]) speaker_gasket();
    translate([0, -e * 1.85, 0]) on_skirt(spk_z) translate([0, 0, -(wall + seal_land + spk_gask + spk_bz)]) rotate([180,0,0]) driver_clamp();
    translate([0,  e * 1.45, 0]) proxy_inlet();
    translate([0, 0, -e * 0.45]) proxy_standoffs();
    translate([0, 0, -e * 0.62]) proxy_pi();
    translate([0, 0, -e * 0.90]) ballast_placed();
    translate([0, 0, -e * 1.45]) base();
    translate([0, 0, -e * 2.10 - foot_t]) foot();
}
module section_view() {
    difference() { assembly(0); translate([W/2, 0, H]) cube([W, D*2, H*2.4], center = true); }
}

// PRINTED PARTS ONLY, laid out for one ordinary bed.
// Shell: TOP FACE ON THE BED. The bottom opening then faces up so the cavity
// has no ceiling; the raked face and skirt are steep walls; the mic ports
// print crisp against the plate. No support anywhere.
// Print plates for a 220 x 220 bed. PLATE 1..4 PETG, 5..6 TPU (6 optional).
// The shell goes TOP FACE DOWN: the bottom opening then faces up so the cavity
// has no ceiling, the raked face and skirt are steep walls, and the mic ports
// print crisp against the plate. No support on any plate.
PLATE = 1;
module bed_outline() {
    color("#394046") linear_extrude(height = 0.8)
        difference() { square([BED_X, BED_Y], center = true); square([BED_X - 4, BED_Y - 4], center = true); }
}
SHOW_BED = true;
module printplate() {
    if (SHOW_BED) bed_outline();
    if (PLATE == 1) rotate([180,0,0]) translate([0,0,-H]) shell();
    if (PLATE == 2) {
        translate([-30, 0, 0]) base();
        translate([78, 0, 0]) rotate([0,0,90]) driver_clamp();
    }
    if (PLATE == 3) {
        translate([-32, -35, 0]) rotate([180,0,0]) translate([0,0,-fp_t]) faceplate();
        translate([30, 70, 0]) mic_clamp_ring();
    }
    if (PLATE == 4 && BALLAST) {
        for (i = [0,1]) translate([-70 + i*44, 0, 0]) ballast_pod();
        for (i = [0,1]) translate([26 + i*36, 0, 0]) ballast_lid();
    }
    // TPU. Plate 5 is required and tiny — 8 g, and it prints fine even on a
    // Bowden machine if you slow it down. Plate 6 (the foot) is optional:
    // stick-on 60A silicone bumpers do the same job if you would rather not
    // run flexible filament at all.
    if (PLATE == 5) {
        translate([-52, 0, 0]) mic_gasket();
        translate([30, 30, 0]) panel_gasket();
        translate([30, -46, 0]) speaker_gasket();
    }
    if (PLATE == 6) foot();
}

// ============================================================================
if      (MODE == "assembly")     assembly(0);
else if (MODE == "exploded")     assembly(EXPLODE);
else if (MODE == "section")      section_view();
else if (MODE == "shell")        shell();
else if (MODE == "faceplate")    faceplate();
else if (MODE == "base")         base();
else if (MODE == "mic_clamp")    mic_clamp_ring();
else if (MODE == "driver_clamp") driver_clamp();
else if (MODE == "ballast_pod")  ballast_pod();
else if (MODE == "ballast_lid")  ballast_lid();
else if (MODE == "mic_gasket")   mic_gasket();
else if (MODE == "speaker_gasket") speaker_gasket();
else if (MODE == "panel_gasket") panel_gasket();
else if (MODE == "foot")         foot();
else if (MODE == "diffuser")     diffuser();
else if (MODE == "printplate")   printplate();
// NOT `else assembly(0)`. A mistyped MODE used to fall through to the whole
// device: cad/mass.py asked for "ballast_pod_optional", got a 918 g assembly,
// and reported a plausible-looking ratio against it. A typo must fail, not
// quietly answer a different question.
else assert(false, str("unknown MODE \"", MODE, "\". See the list at the top ",
                       "of this file — a typo used to render the whole device."));
