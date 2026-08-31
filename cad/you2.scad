// ============================================================================
//  YOU2 — desk-based local voice assistant
//  Parametric enclosure. OpenSCAD 2021.01+
//
//  rev B — resized around the Waveshare 3.4-DSI-TOUCH-C (115 x 115 carrier,
//          Ø87.6 glass) and a 102 mm NeoPixel light bar. Rev A was modelled
//          around a Ø60 module and a ring, which do not exist at this size.
//
//  ✔ Renders clean. Both parts export as manifold solids (Status: NoError).
//    ⚠ But every part dimension came from a datasheet, not calipers. Measure
//    your actual display, LED sticks and mic array before printing — vendor
//    listings for these parts are routinely wrong.
//
//  Coordinate convention:
//    X = width  (left <-> right), centred on 0
//    Y = depth  (front = -Y, back = +Y)
//    Z = height (0 = desk surface)
// ============================================================================

$fn = $preview ? 48 : 120;

// MODE: "assembly" | "exploded" | "shell" | "base" | "section" | "faceplate"
MODE    = "exploded";
EXPLODE = 55;

/* [Overall form] ---------------------------------------------------------- */
width       = 134;    // 115 carrier + walls + clearance
depth       = 125;
height      = 165;    // skirt + enough raked face for a 115 mm carrier
wall        = 2.4;
corner_r    = 9;
face_angle  = 18;     // raked face lean-back from vertical
skirt_h     = 46;     // vertical front: speaker + light bar both live here

/* [Display — Waveshare 3.4-DSI-TOUCH-C] ------------------------------------ */
disp_carrier   = 115.0;   // square carrier PCB, both axes
disp_glass_od  = 87.6;    // visible round active area
disp_pcb_t     = 1.8;
disp_stack_t   = 6.5;     // carrier + panel + FFC bend clearance
disp_z         = skirt_h + (height - skirt_h)/2;  // centred on the raked face
disp_recess    = 1.2;

/* [LED light bar — 2x NeoPixel Stick 8] ------------------------------------ */
// A ring cannot encircle an 87.6 mm display; largest stocked ring is 65.5 OD.
bar_len        = 102.0;   // two 8-LED sticks butted
bar_w          = 10.2;
bar_slot_w     = 4.5;     // diffuser slot, print in translucent PETG
bar_z          = skirt_h - 6;   // on the skirt, just below the shoulder

/* [Speaker — Seeed enclosed 4R 5W, in the skirt] --------------------------- */
spk_d          = 36;      // grille field; driver behind it is larger
spk_body_d     = 44;
spk_body_t     = 18;
spk_z          = 18;
grille_hole_d  = 2.6;
grille_pitch   = 5.0;
spk_chamber_r  = 30;      // sealed volume radius around the driver

/* [Microphones — reSpeaker Flex Circular-4, 73 mm PCB on FPC] -------------- */
mic_pcb_d      = 73.0;
mic_circle_d   = 58.0;    // capsule pitch circle on that PCB
mic_port_d     = 3.2;
mic_inset_y    = 16;      // pushed back, away from the driver
mic_pocket_t   = 2.2;

/* [Compute board — Raspberry Pi 5] ----------------------------------------- */
pi_w           = 85;
pi_d           = 56;
pi_hole_dx     = 58;
pi_hole_dy     = 49;
pi_standoff_h  = 6;
pi_standoff_od = 6.5;
pi_screw_d     = 2.7;     // tap for M3

/* [Base plate] ------------------------------------------------------------- */
base_t         = 3.0;
base_inset     = 0.3;
foot_d         = 12;
foot_h         = 2.5;

/* [Rear I/O — ALL venting is rear-only. Front or top vents leak speaker
    output straight into the mic array. ] ------------------------------------ */
vent_w         = 78;
vent_h         = 46;
vent_slot_w    = 3.2;
vent_pitch     = 6.4;
vent_z_lo      = 46;      // low intake
vent_z_hi      = 118;     // high exhaust, fins run vertically between them

// ============================================================================
//  DERIVED
// ============================================================================
raked_rise  = height - skirt_h;
setback     = raked_rise * tan(face_angle);
raked_slope = raked_rise / cos(face_angle);   // true face length
face_margin = (raked_slope - disp_carrier) / 2;

echo(str("raked face slope length = ", raked_slope, " mm"));
echo(str("margin around 115 mm carrier = ", face_margin, " mm each end"));

// ============================================================================
//  HELPERS
// ============================================================================

// 2D side profile in (Y,Z): vertical skirt, then raked face.
module side_profile() {
    offset(r = corner_r) offset(delta = -corner_r)
        polygon([
            [-depth/2,           0       ],   // bottom front
            [ depth/2,           0       ],   // bottom back
            [ depth/2,           height  ],   // top back
            [-depth/2 + setback, height  ],   // top front
            [-depth/2,           skirt_h ]    // skirt shoulder
        ]);
}

module plan_mask(w, d, h) {
    linear_extrude(height = h, center = true)
        offset(r = corner_r) offset(delta = -corner_r)
            square([w, d], center = true);
}

module body_solid(shrink = 0) {
    intersection() {
        rotate([90, 0, 90])
            linear_extrude(height = width - 2*shrink, center = true)
                offset(delta = -shrink) side_profile();
        plan_mask(width - 2*shrink, depth * 2, height * 3);
    }
}

// Place children on the raked face. z = height above the desk.
module on_raked_face(z) {
    translate([0, -depth/2 + (z - skirt_h) * tan(face_angle), z])
        rotate([90 - face_angle, 0, 0])
            children();
}

// Place children on the vertical skirt.
module on_skirt(z) {
    translate([0, -depth/2, z])
        rotate([90, 0, 0])
            children();
}

module grille(field_d, hole_d, pitch, depth_cut) {
    n_rows = ceil(field_d / (pitch * 0.866) / 2) + 1;
    n_cols = ceil(field_d / pitch / 2) + 1;
    intersection() {
        cylinder(d = field_d, h = depth_cut, center = true);
        for (r = [-n_rows : 1 : n_rows], c = [-n_cols : 1 : n_cols])
            translate([ (c + (r % 2 == 0 ? 0 : 0.5)) * pitch,
                        r * pitch * 0.866, 0 ])
                cylinder(d = hole_d, h = depth_cut + 1, center = true);
    }
}

// ============================================================================
//  CUTOUTS
// ============================================================================

// Round aperture for the glass only. The 115 x 115 carrier does NOT need a
// pocket — the shell is already hollow behind the face. Cutting a square
// pocket here removes the whole front panel.
module display_cut() {
    on_raked_face(disp_z)
        translate([0, 0, -(wall + 2)])
            cylinder(d = disp_glass_od, h = wall + 4);
}

// Slot on the raked face, just above the shoulder. Cutting it into the
// rounded shoulder itself gives a ragged double-edged opening — cut it
// perpendicular to a flat face instead.
module led_bar_cut() {
    on_skirt(bar_z) {
        // diffuser slot through the wall
        translate([-bar_len/2, -bar_slot_w/2, -(wall + 2)])
            cube([bar_len, bar_slot_w, wall + 4]);
        // pocket behind it for the two sticks (local -Z is into the shell)
        translate([-bar_len/2 - 1, -bar_w/2, -(wall + 2.4)])
            cube([bar_len + 2, bar_w, 2.4]);
    }
}

module speaker_cut() {
    on_skirt(spk_z) {
        translate([0, 0, -1]) grille(spk_d - 3, grille_hole_d, grille_pitch, wall + 4);
        // driver body pocket, inside the sealed chamber
        translate([0, 0, -wall - spk_body_t]) cylinder(d = spk_body_d, h = spk_body_t + 0.2);
    }
}

module mic_cut() {
    translate([0, mic_inset_y, height - wall - 1]) {
        for (i = [0 : 3])
            rotate([0, 0, i * 90 + 45])
                translate([mic_circle_d/2, 0, 0])
                    // slight outer chamfer so the port does not whistle
                    union() {
                        cylinder(d = mic_port_d, h = wall + 4);
                        translate([0, 0, wall - 0.6])
                            cylinder(d1 = mic_port_d, d2 = mic_port_d + 1.6, h = 0.8);
                    }
        // recessed pocket for the 73 mm FPC array PCB
        translate([0, 0, -mic_pocket_t - wall + 1])
            cylinder(d = mic_pcb_d + 0.6, h = mic_pocket_t);
    }
}

module rear_cut() {
    // low intake + high exhaust, both on the rear face only
    for (z = [vent_z_lo, vent_z_hi])
        translate([0, depth/2 - wall - 1, z])
            rotate([-90, 0, 0])
                for (i = [-floor(vent_w/vent_pitch/2) : 1 : floor(vent_w/vent_pitch/2)])
                    translate([i * vent_pitch, 0, 0])
                        hull() {
                            translate([0,  vent_h/4 - vent_slot_w/2, 0]) cylinder(d = vent_slot_w, h = wall + 4);
                            translate([0, -vent_h/4 + vent_slot_w/2, 0]) cylinder(d = vent_slot_w, h = wall + 4);
                        }

    // Pi I/O window. One generous opening — port positions shift between
    // board revisions. Print a snap-in bezel once you can measure the board.
    translate([0, depth/2 - wall - 1, 18])
        rotate([-90, 0, 0])
            hull()
                for (sx = [-1, 1], sz = [0, 1])
                    translate([sx * 44, sz * 16, 0]) cylinder(d = 5, h = wall + 4);
}

// ============================================================================
//  PARTS
// ============================================================================

module shell() {
    difference() {
        difference() {
            body_solid();
            // Do NOT translate the cavity up — that lifts the ceiling through
            // the top face. Wall thickness all round; the bottom is opened
            // separately so the base plate can drop in.
            body_solid(shrink = wall);
            translate([0, 0, -1]) plan_mask(width - 2*wall, depth - 2*wall, base_t + 2);
        }
        display_cut();
        led_bar_cut();
        speaker_cut();
        mic_cut();
        rear_cut();
    }

    // --- sealed speaker chamber -------------------------------------------
    // Isolates driver energy from the mic array. Mount the driver on a
    // compliant gasket; do not let the shell couple to it directly.
    // NB: on_skirt maps local +Z to -Y (out of the box). The chamber has to be
    // built along local -Z or it lands outside the shell and intersects to
    // nothing.
    intersection() {
        body_solid(shrink = wall + 0.01);
        on_skirt(spk_z)
            translate([0, 0, -(spk_body_t + 8)])
                difference() {
                    cylinder(r = spk_chamber_r, h = spk_body_t + 8);
                    translate([0, 0, -1]) cylinder(r = spk_chamber_r - 2.2, h = spk_body_t + 10);
                }
    }

    // --- stiffening frame behind the display -------------------------------
    intersection() {
        body_solid(shrink = wall + 0.01);
        on_raked_face(disp_z)
            translate([0, 0, -disp_stack_t - 2.4])
                difference() {
                    linear_extrude(height = 2.4)
                        square([disp_carrier + 8, disp_carrier + 8], center = true);
                    translate([0, 0, -1]) linear_extrude(height = 4.4)
                        square([disp_carrier - 6, disp_carrier - 6], center = true);
                }
    }
}

module base() {
    difference() {
        union() {
            linear_extrude(height = base_t)
                offset(r = corner_r) offset(delta = -corner_r)
                    square([width - 2*wall - 2*base_inset,
                            depth - 2*wall - 2*base_inset], center = true);
            translate([0, 8, base_t])
                for (sx = [-1, 1], sy = [-1, 1])
                    translate([sx * pi_hole_dx/2, sy * pi_hole_dy/2, 0])
                        cylinder(d = pi_standoff_od, h = pi_standoff_h);
        }
        translate([0, 8, base_t - 1])
            for (sx = [-1, 1], sy = [-1, 1])
                translate([sx * pi_hole_dx/2, sy * pi_hole_dy/2, 0])
                    cylinder(d = pi_screw_d, h = pi_standoff_h + 2);

        // convection intake under the board
        for (i = [-3 : 1 : 3])
            translate([i * 15, 8, -1])
                hull() {
                    translate([0,  17, 0]) cylinder(d = 6, h = base_t + 2);
                    translate([0, -17, 0]) cylinder(d = 6, h = base_t + 2);
                }

        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * (width/2 - 24), sy * (depth/2 - 24), -0.1])
                cylinder(d = foot_d, h = foot_h);
    }
}

// --- proxies, fit-check only ------------------------------------------------
module proxy_pi() {
    color("#2e7d32", .85)
        translate([0, 8, base_t + pi_standoff_h])
            cube([pi_w, pi_d, 1.6], center = true);
}
module proxy_display() {
    on_raked_face(disp_z) translate([0, 0, -disp_stack_t]) {
        color("#1e5f2e", .9)                                   // carrier PCB
            linear_extrude(height = disp_pcb_t)
                square([disp_carrier, disp_carrier], center = true);
        color("#0b0e12", 1)                                    // panel body
            cylinder(d = disp_glass_od, h = disp_stack_t - 0.6);
        color("#2fa4d8", 1)                                    // lit glass
            translate([0, 0, disp_stack_t - 0.6])
                cylinder(d = disp_glass_od - 1.2, h = 0.7);
    }
}
module proxy_bar() {
    color("#ffb300", .7)
        on_skirt(bar_z)
            translate([-bar_len/2, -bar_w/2, -(wall + 2.0)])
                cube([bar_len, bar_w, 1.6]);
}
module proxy_speaker() {
    color("#424242", .9)
        on_skirt(spk_z) translate([0, 0, -wall - spk_body_t]) cylinder(d = spk_body_d, h = spk_body_t);
}
module proxy_mics() {
    color("#7e57c2", .8)
        translate([0, mic_inset_y, height - wall - mic_pocket_t])
            cylinder(d = mic_pcb_d, h = 1.2);
}

// ============================================================================
//  ASSEMBLIES
// ============================================================================

// Parts separate along their own install axes: face-mounted parts along the
// raked-face normal, skirt parts straight forward, mics up, base down.
FN = [0, -cos(face_angle), sin(face_angle)];   // raked face outward normal

module assembly(e = 0) {
    shell();
    translate([0, 0, -e * 1.6]) base();
    translate([0, 0, -e * 0.9]) proxy_pi();
    translate(FN * e * 1.5)     proxy_display();
    translate([0, -e * 2.2, -e * 0.35]) proxy_bar();
    translate([0, -e * 1.1, 0]) proxy_speaker();
    translate([0, 0, e * 0.8])  proxy_mics();
}

module section_view() {
    difference() {
        assembly(0);
        translate([width/2, 0, height]) cube([width, depth * 2, height * 2], center = true);
    }
}

// ============================================================================
//  RENDER
// ============================================================================
if      (MODE == "assembly")  assembly(0);
else if (MODE == "exploded")  assembly(EXPLODE);
else if (MODE == "shell")     shell();
else if (MODE == "base")      base();
else if (MODE == "section")   section_view();
else if (MODE == "faceplate") intersection() { shell(); on_raked_face(disp_z) cylinder(d = 150, h = 60, center = true); }
else                          assembly(0);
