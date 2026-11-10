// stemn-ui.js: the device surface, drawn in [jsui] (loomn design language).
//
// inlet 0:  ui <json> from stemn-live.js
// outlet 0: stem <name> <0|1>, run
//
// Nothing jumps: every element has a fixed place and size in every state.

mgraphics.init();
mgraphics.relative_coords = 0;
mgraphics.autofill = 0;

inlets = 1;
outlets = 1;

var BG = [0.078, 0.078, 0.078, 1]; // #141414
var SURFACE = [0.11, 0.106, 0.102, 1]; // #1C1B1A
var RAISED = [0.137, 0.133, 0.129, 1]; // #232221
var RAISED2 = [0.165, 0.161, 0.157, 1]; // #2A2928
var LINE = [0.196, 0.192, 0.184, 1]; // #32312F
var TEXT = [0.941, 0.933, 0.914, 1]; // #F0EEE9
var MUTED = [0.561, 0.549, 0.525, 1]; // #8F8C86
var ACCENT = [1, 0.353, 0.122, 1]; // #FF5A1F
var ACCENT_OFF = [0.29, 0.165, 0.118, 1]; // #4a2a1e
var ACCENT_OFF_TEXT = [0.541, 0.416, 0.369, 1]; // #8a6a5e
var DANGER = [0.71, 0.282, 0.231, 1]; // #B5483B
var DARK = [0.078, 0.078, 0.078, 1];

var STEMS = ["kick", "snare", "hihat", "toms", "ride", "crash"];
var LABELS = { kick: "Kick", snare: "Snare", hihat: "Hi-Hat", toms: "Toms", ride: "Ride", crash: "Crash" };
var KENN = {
    kick: [0.769, 0.412, 0.298, 1],
    snare: [0.761, 0.631, 0.306, 1],
    hihat: [0.373, 0.624, 0.69, 1],
    toms: [0.541, 0.482, 0.706, 1],
    ride: [0.435, 0.663, 0.514, 1],
    crash: [0.678, 0.447, 0.6, 1]
};

var FONT = "Ableton Sans Medium";
var FONT_BOLD = "Ableton Sans Bold";

var state = {
    app: -1,
    source: null,
    stems: { kick: 1, snare: 1, hihat: 1, toms: 1, ride: 1, crash: 1 },
    running: 0,
    progress: 0,
    note: "",
    error: 0
};
var hover = "";

// layout (device height is 169 px)
var PAD = 12;
function W() {
    return box.rect[2] - box.rect[0];
}
function dropRect() {
    return [PAD, 32, W() - 2 * PAD, 44];
}
function chipRect(i) {
    var gap = 6;
    var w = (W() - 2 * PAD - gap * 5) / 6;
    return [PAD + i * (w + gap), 86, w, 22];
}
function buttonRect() {
    return [PAD, 116, W() - 2 * PAD, 28];
}

function color(c, alpha) {
    mgraphics.set_source_rgba(c[0], c[1], c[2], alpha === undefined ? c[3] : alpha);
}

function rounded(r, radius) {
    mgraphics.rectangle_rounded(r[0], r[1], r[2], r[3], radius, radius);
}

function text(s, x, y, font, size, c, alpha) {
    mgraphics.select_font_face(font);
    mgraphics.set_font_size(size);
    color(c, alpha);
    mgraphics.move_to(x, y);
    mgraphics.show_text(s);
}

function measure(s, font, size) {
    mgraphics.select_font_face(font);
    mgraphics.set_font_size(size);
    return mgraphics.text_measure(s)[0];
}

function fit(s, font, size, width) {
    if (measure(s, font, size) <= width) return s;
    while (s.length > 1 && measure(s + "…", font, size) > width) s = s.slice(0, -1);
    return s + "…";
}

function spaced(s, size, x, y, c, alpha) {
    // small caps label with letter spacing (+8 %)
    mgraphics.select_font_face(FONT_BOLD);
    mgraphics.set_font_size(size);
    color(c, alpha);
    for (var i = 0; i < s.length; i++) {
        mgraphics.move_to(x, y);
        mgraphics.show_text(s[i]);
        x += mgraphics.text_measure(s[i])[0] + size * 0.08;
    }
    return x;
}

function dashed(r, radius, c) {
    mgraphics.set_line_width(1);
    color(c);
    mgraphics.set_dash([4, 3], 0);
    mgraphics.rectangle_rounded(r[0] + 0.5, r[1] + 0.5, r[2] - 1, r[3] - 1, radius, radius);
    mgraphics.stroke();
    mgraphics.set_dash([], 0);
}

function paint() {
    var w = W();
    var h = box.rect[3] - box.rect[1];
    color(BG);
    mgraphics.rectangle(0, 0, w, h);
    mgraphics.fill();

    // logo: name in lower case, orange dot top right of the n
    var lw = measure("stemn", FONT_BOLD, 15);
    text("stemn", PAD, 21, FONT_BOLD, 15, TEXT);
    color(ACCENT);
    mgraphics.ellipse(PAD + lw + 2, 8.5, 4, 4);
    mgraphics.fill();

    // section title right
    var title = "DRUMS TRENNEN";
    var tw = 0;
    mgraphics.select_font_face(FONT_BOLD);
    mgraphics.set_font_size(9);
    for (var t = 0; t < title.length; t++) tw += mgraphics.text_measure(title[t])[0] + 9 * 0.08;
    spaced(title, 9, w - PAD - tw, 20, MUTED);

    drawSource();
    for (var i = 0; i < STEMS.length; i++) drawChip(i);
    drawButton();

    // hint line
    var note = state.note || "";
    text(fit(note, FONT, 10, w - 2 * PAD), PAD, 160, FONT, 10, state.error ? DANGER : MUTED);
}

function drawSource() {
    var r = dropRect();
    color(SURFACE);
    rounded(r, 8);
    mgraphics.fill();
    if (!state.source) {
        dashed(r, 8, LINE);
        var s = "Audio-Clip auswählen oder Loop hierher ziehen";
        var sw = measure(s, FONT, 11);
        text(s, r[0] + (r[2] - sw) / 2, r[1] + 26, FONT, 11, MUTED);
        return;
    }
    spaced(state.source.kind === "clip" ? "CLIP" : "DATEI", 8, r[0] + 12, r[1] + 17, MUTED);
    text(fit(state.source.name, FONT_BOLD, 13, r[2] - 24), r[0] + 12, r[1] + 34, FONT_BOLD, 13, TEXT);
}

function drawChip(i) {
    var r = chipRect(i);
    var name = STEMS[i];
    var on = state.stems[name];
    var dim = state.running ? 0.35 : 1;
    color(hover === name && !state.running ? RAISED2 : RAISED);
    rounded(r, 6);
    mgraphics.fill();
    if (on) {
        mgraphics.set_line_width(1);
        color(ACCENT, dim);
        mgraphics.rectangle_rounded(r[0] + 0.5, r[1] + 0.5, r[2] - 1, r[3] - 1, 6, 6);
        mgraphics.stroke();
    }
    // Kennfarbe as a small dot, hollow when the stem is off
    var cx = r[0] + 9;
    var cy = r[1] + r[3] / 2;
    if (on) {
        color(KENN[name], dim);
        mgraphics.ellipse(cx - 3, cy - 3, 6, 6);
        mgraphics.fill();
    } else {
        mgraphics.set_line_width(1);
        color(MUTED, 0.6 * dim);
        mgraphics.ellipse(cx - 2.5, cy - 2.5, 5, 5);
        mgraphics.stroke();
    }
    var label = fit(LABELS[name], FONT, 10, r[2] - 20);
    text(label, r[0] + 16, r[1] + 15, FONT, 10, on ? TEXT : MUTED, dim);
}

function drawButton() {
    var r = buttonRect();
    var usable = state.app !== 0 && state.source;
    if (state.running) {
        color(RAISED);
        rounded(r, 6);
        mgraphics.fill();
        var p = Math.max(0, Math.min(1, state.progress));
        if (p > 0) {
            mgraphics.save();
            mgraphics.rectangle(r[0], r[1], r[2] * p, r[3]);
            mgraphics.clip();
            color(ACCENT_OFF);
            rounded(r, 6);
            mgraphics.fill();
            mgraphics.restore();
        }
        // fixed-width percent so the text does not move
        var pct = Math.round(p * 100);
        var label = (pct < 10 ? "  " : pct < 100 ? " " : "") + pct + " %  ·  Abbrechen";
        var lw = measure("100 %  ·  Abbrechen", FONT_BOLD, 11);
        text(label, r[0] + (r[2] - lw) / 2, r[1] + 18, FONT_BOLD, 11, hover === "button" ? TEXT : MUTED);
        // thin orange line shows the progress clearly
        color(ACCENT);
        mgraphics.rectangle(r[0] + 6, r[1] + r[3] - 3, (r[2] - 12) * p, 2);
        mgraphics.fill();
        return;
    }
    color(usable ? (hover === "button" ? [0.925, 0.4, 0.212, 1] : ACCENT) : ACCENT_OFF);
    rounded(r, 6);
    mgraphics.fill();
    mgraphics.select_font_face(FONT_BOLD);
    mgraphics.set_font_size(12);
    var word = "TRENNEN";
    var ww = 0;
    for (var i = 0; i < word.length; i++) ww += mgraphics.text_measure(word[i])[0] + 1;
    spaced(word, 12, r[0] + (r[2] - ww) / 2, r[1] + 18, usable ? DARK : ACCENT_OFF_TEXT);
}

// ------------------------------------------------------------------ input

function inside(r, x, y) {
    return x >= r[0] && x <= r[0] + r[2] && y >= r[1] && y <= r[1] + r[3];
}

function hit(x, y) {
    if (inside(buttonRect(), x, y)) return "button";
    for (var i = 0; i < STEMS.length; i++) if (inside(chipRect(i), x, y)) return STEMS[i];
    return "";
}

function onclick(x, y) {
    var what = hit(x, y);
    if (what === "button") {
        outlet(0, "run");
    } else if (what && !state.running) {
        outlet(0, "stem", what, state.stems[what] ? 0 : 1);
    }
}

function onidle(x, y) {
    var what = hit(x, y);
    if (what !== hover) {
        hover = what;
        mgraphics.redraw();
    }
}

function onidleout() {
    if (hover) {
        hover = "";
        mgraphics.redraw();
    }
}

function ui() {
    try {
        state = JSON.parse(arrayfromargs(arguments).join(" "));
    } catch (e) {
        return;
    }
    mgraphics.redraw();
}

function onresize() {
    mgraphics.redraw();
}
