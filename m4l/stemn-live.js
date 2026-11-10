// stemn-live.js: runs inside [js] in the Max for Live device.
//
// Holds the device state, follows the selected clip in Live, talks to the
// runner (node.script) and, when the stems are ready, puts each stem on a new
// audio track right below the source, at the same place as the source clip.
//
// inlet 0:  bang (device loaded), drop <path>, stem <name> <0|1>, run,
//           app/progress/done/error/cancelled/ready (from the runner)
// outlet 0: to the runner (check, separate <json>, cancel)
// outlet 1: to the jsui (ui <json>)

autowatch = 1;
inlets = 1;
outlets = 2;

var STEMS = ["kick", "snare", "hihat", "toms", "ride", "crash"];
var LABELS = { kick: "Kick", snare: "Snare", hihat: "Hi-Hat", toms: "Toms", ride: "Ride", crash: "Crash" };
var COLORS = { kick: 0xc4694c, snare: 0xc2a14e, hihat: 0x5f9fb0, toms: 0x8a7bb4, ride: 0x6fa983, crash: 0xad7299 };

var state = {
    app: -1, // -1 unknown, 0 missing, 1 found
    source: null, // {kind: "clip"|"file", name, path}
    stems: { kick: 1, snare: 1, hihat: 1, toms: 1, ride: 1, crash: 1 },
    running: 0,
    progress: 0,
    note: "",
    error: 0
};
var sourceClip = null; // {clipPath, trackIndex, arrangement, slot} while the source is a clip
var running = null; // the source at the time TRENNEN was pressed
var viewObserver = null;
var liveReady = 0;

function push() {
    outlet(1, "ui", JSON.stringify(state));
}

function hint(text, isError) {
    state.note = text;
    state.error = isError ? 1 : 0;
    push();
}

function idle() {
    if (state.app === 0) return hint("stemn.app im Programme-Ordner installieren", 1);
    if (!state.source) return hint("Audio-Clip auswählen oder Loop hierher ziehen");
    hint("TRENNEN legt jede Spur als neue Audiospur darunter an");
}

function str(value) {
    // LiveAPI.get returns an array; strings with spaces can arrive in pieces
    if (value === undefined || value === null) return "";
    if (value instanceof Array) return value.join(" ");
    return String(value);
}

function num(value) {
    if (value instanceof Array) value = value[0];
    return Number(value);
}

function basename(p) {
    var name = String(p).split("/").pop();
    return name.replace(/\.[^.]+$/, "");
}

// ------------------------------------------------------------------ Live side

function bang() {
    // live.thisdevice: the Live API is ready now
    liveReady = 1;
    if (!viewObserver) {
        viewObserver = new LiveAPI(onViewChange, "live_set view");
        viewObserver.property = "detail_clip";
    }
    readSelectedClip();
    outlet(0, "check");
    idle();
}

function onViewChange(args) {
    if (!liveReady || state.running) return;
    if (args && args[0] === "detail_clip") readSelectedClip();
}

function trackIndexOf(path) {
    var m = /live_set tracks (\d+)/.exec(path);
    return m ? parseInt(m[1], 10) : -1;
}

function readSelectedClip() {
    var clip = new LiveAPI("live_set view detail_clip");
    if (!clip || clip.id == 0) return;
    if (!num(clip.get("is_audio_clip"))) return;
    var file = str(clip.get("file_path"));
    if (!file) return;
    var clipPath = clip.unquotedpath;
    var track = trackIndexOf(clipPath);
    if (track < 0) return; // return or master track: no new tracks next to it
    var slot = /clip_slots (\d+)/.exec(clipPath);
    sourceClip = {
        clipPath: clipPath,
        trackIndex: track,
        arrangement: /arrangement_clips/.test(clipPath) ? 1 : 0,
        slot: slot ? parseInt(slot[1], 10) : -1
    };
    state.source = { kind: "clip", name: str(clip.get("name")) || basename(file), path: file };
    idle();
}

function drop() {
    var p = arrayfromargs(arguments).join(" ");
    if (!p || state.running) return;
    sourceClip = null;
    state.source = { kind: "file", name: basename(p), path: p };
    idle();
}

function deviceTrackIndex() {
    var api = new LiveAPI("this_device");
    for (var i = 0; i < 8 && api.id != 0; i++) {
        var t = trackIndexOf(api.unquotedpath);
        if (t >= 0) return t;
        api = new LiveAPI(api.get("canonical_parent").join(" "));
    }
    return -1;
}

function sessionVisible() {
    try {
        return num(new LiveAPI("live_app view").call("is_view_visible", "Session")) ? 1 : 0;
    } catch (e) {
        return 1;
    }
}

function selectedSceneIndex() {
    var scene = new LiveAPI("live_set view selected_scene");
    var m = /scenes (\d+)/.exec(scene.unquotedpath);
    return m ? parseInt(m[1], 10) : 0;
}

function safeSet(api, prop, value) {
    try {
        api.set(prop, value);
    } catch (e) {}
}

function copyClip(src, dst) {
    // same content, same region: the stems are sample-accurate to the source file
    var warping = num(src.get("warping"));
    safeSet(dst, "warping", warping);
    if (warping) {
        safeSet(dst, "warp_mode", num(src.get("warp_mode")));
        copyWarpMarkers(src, dst);
    }
    safeSet(dst, "gain", num(src.get("gain")));
    var looping = num(src.get("looping"));
    safeSet(dst, "looping", looping);
    // widen first, then narrow, so no value is ever outside the current range
    safeSet(dst, "end_marker", num(src.get("end_marker")));
    safeSet(dst, "start_marker", num(src.get("start_marker")));
    safeSet(dst, "end_marker", num(src.get("end_marker")));
    safeSet(dst, "loop_end", num(src.get("loop_end")));
    safeSet(dst, "loop_start", num(src.get("loop_start")));
    safeSet(dst, "loop_end", num(src.get("loop_end")));
}

function warpMarkers(clip) {
    try {
        var data = JSON.parse(str(clip.get("warp_markers")));
        return data.warp_markers || [];
    } catch (e) {
        return [];
    }
}

function copyWarpMarkers(src, dst) {
    var want = warpMarkers(src);
    if (!want.length) return;
    var keep = {};
    for (var i = 0; i < want.length; i++) {
        keep[want[i].beat_time.toFixed(6)] = 1;
        try {
            var d = new Dict();
            d.set("beat_time", want[i].beat_time);
            d.set("sample_time", want[i].sample_time);
            dst.call("add_warp_marker", d);
        } catch (e) {}
    }
    var have = warpMarkers(dst);
    for (var j = 0; j < have.length; j++) {
        if (!keep[have[j].beat_time.toFixed(6)]) {
            try {
                dst.call("remove_warp_marker", have[j].beat_time);
            } catch (e) {}
        }
    }
}

function placeStems(files) {
    var song = new LiveAPI("live_set");
    var src = running.clip ? new LiveAPI(running.clip.clipPath) : null;
    if (src && src.id == 0) src = null;
    var base = running.clip ? running.clip.trackIndex : deviceTrackIndex();
    if (base < 0) base = num(song.getcount("tracks")) - 1;
    var arrangement, slot, start;
    if (src) {
        arrangement = running.clip.arrangement;
        slot = running.clip.slot;
        start = num(src.get("start_time"));
    } else {
        arrangement = sessionVisible() ? 0 : 1;
        slot = selectedSceneIndex();
        start = Math.floor(num(song.get("current_song_time")));
    }
    var made = 0;
    for (var i = 0; i < STEMS.length; i++) {
        var stem = STEMS[i];
        if (!files[stem]) continue;
        var index = base + 1 + made;
        song.call("create_audio_track", index);
        var track = new LiveAPI("live_set tracks " + index);
        track.set("name", running.name + " " + LABELS[stem]);
        safeSet(track, "color", COLORS[stem]);
        var clip = null;
        if (arrangement) {
            track.call("create_audio_clip", files[stem], start);
            var count = num(track.getcount("arrangement_clips"));
            for (var k = count - 1; k >= 0; k--) {
                var c = new LiveAPI("live_set tracks " + index + " arrangement_clips " + k);
                if (Math.abs(num(c.get("start_time")) - start) < 1e-6) {
                    clip = c;
                    break;
                }
            }
        } else {
            new LiveAPI("live_set tracks " + index + " clip_slots " + slot).call("create_audio_clip", files[stem]);
            clip = new LiveAPI("live_set tracks " + index + " clip_slots " + slot + " clip");
        }
        if (clip && clip.id != 0) {
            safeSet(clip, "name", LABELS[stem]);
            safeSet(clip, "color", COLORS[stem]);
            if (src) copyClip(src, clip);
        }
        made++;
    }
    if (src && made) safeSet(src, "muted", 1); // the stems play instead of the loop
    return made;
}

// ------------------------------------------------------------------ UI side

function stem(name, on) {
    if (!(name in state.stems) || state.running) return;
    state.stems[name] = on ? 1 : 0;
    push();
}

function run() {
    if (state.running) {
        outlet(0, "cancel");
        hint("Wird abgebrochen …");
        return;
    }
    if (state.app === 0 || !state.source) return idle();
    var wanted = [];
    for (var i = 0; i < STEMS.length; i++) if (state.stems[STEMS[i]]) wanted.push(STEMS[i]);
    if (!wanted.length) return hint("Mindestens eine Spur auswählen", 1);
    running = { name: state.source.name, clip: state.source.kind === "clip" ? sourceClip : null };
    state.running = 1;
    state.progress = 0;
    hint("Modell wird geladen");
    outlet(0, "separate", JSON.stringify({ input: state.source.path, stems: wanted }));
}

// ------------------------------------------------------------------ runner side

function app(found) {
    state.app = found ? 1 : 0;
    if (!state.running) idle();
}

function ready() {
    outlet(0, "check");
}

function progress(value) {
    if (!state.running) return;
    state.progress = Number(value) || 0;
    var note = arrayfromargs(arguments).slice(1).join(" ");
    state.note = note || "Wird getrennt";
    state.error = 0;
    push();
}

function done(json) {
    state.running = 0;
    state.progress = 0;
    var files = {};
    try {
        files = JSON.parse(arrayfromargs(arguments).join(" "));
    } catch (e) {
        return hint("Ergebnis nicht lesbar", 1);
    }
    var made = 0;
    try {
        made = placeStems(files);
    } catch (e) {
        return hint("Stems liegen im Ordner neben dem Loop, Spuren anlegen ging nicht", 1);
    }
    hint(made === 1 ? "Fertig: 1 neue Spur" : "Fertig: " + made + " neue Spuren");
}

function error() {
    state.running = 0;
    state.progress = 0;
    hint(arrayfromargs(arguments).join(" "), 1);
}

function cancelled() {
    state.running = 0;
    state.progress = 0;
    hint("Abgebrochen");
}

function loadbang() {
    push();
}
