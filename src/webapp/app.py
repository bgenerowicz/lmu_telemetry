"""LMU Lap Analyser web app (Plotly Dash), run locally by the desktop launcher.

Flow: pick a track and car, point it at your telemetry folder, pick one of
your laps and what to compare it against (an expert lap, your own best lap,
or your fastest time through each section), and read the analysis.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dash import (
    ALL,
    ClientsideFunction,
    Dash,
    Input,
    Output,
    State,
    dash_table,
    dcc,
    html,
    no_update,
)

from lmu_core.analysis import Finding, SessionAnalysis, analyse
from lmu_core.geometry import track_geometry
from lmu_core.library import (
    load_lap,
    load_reference,
    load_references,
    save_reference,
    scan_folder,
)
from webapp.charts import map_figure, traces_figure

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
SELF_BEST = "self:best"
SELF_SECTIONS = "self:sections"
# Laps used for "your fastest sections"; more adds little and costs time.
MAX_SECTION_LAPS = 30


def create_app(
    reference_dirs: list[Path],
    user_reference_dir: Path,
    initial_folder: str = "",
    on_folder_scanned: Callable[[str], None] | None = None,
    choose_folder: Callable[[str | None], str | None] | None = None,
) -> Dash:
    # Results (graphs, findings) are created by a callback, so callbacks on them
    # are registered before the components exist.
    app = Dash(__name__, title="LMU Lap Analyser", update_title=None,
               suppress_callback_exceptions=True)
    app.index_string = _INDEX.replace("{sync_js}", _SYNC_JS)
    all_ref_dirs = [*reference_dirs, user_reference_dir]

    def references():
        return load_references([d for d in all_ref_dirs if Path(d).is_dir()])[0]

    app.layout = html.Div(
        className="page",
        children=[
            dcc.Store(id="recordings", data=[]),
            dcc.Store(id="refs-version", data=0),
            # Outputs for the browser-side callbacks that link traces and map.
            dcc.Store(id="sync-hover"), dcc.Store(id="sync-zoom"),
            dcc.Store(id="sync-focus"), dcc.Store(id="sync-reset"),
            html.Header([
                html.H1("LMU Lap Analyser"),
                html.P("Compare your Le Mans Ultimate laps with an expert lap, corner by corner.",
                       className="muted"),
            ]),
            html.Section(className="panel steps", children=[
                html.Div(className="step", children=[
                    html.Div("1. Track and car", className="step-title"),
                    dcc.Dropdown(id="track", placeholder="Track", clearable=False),
                    dcc.Dropdown(id="car", placeholder="Car", clearable=False,
                                 className="mt"),
                ]),
                html.Div(className="step", children=[
                    html.Div("2. Your telemetry folder", className="step-title"),
                    html.Div(className="row", children=[
                        dcc.Input(id="folder", value=initial_folder, debounce=True,
                                  placeholder=r"...\Le Mans Ultimate\UserData\Telemetry",
                                  className="grow"),
                        html.Button("Browse…", id="browse", className="btn",
                                    style={} if choose_folder else {"display": "none"}),
                        html.Button("Rescan", id="rescan", className="btn"),
                    ]),
                    html.Div(id="scan-status", className="muted mt"),
                ]),
            ]),
            html.Section(className="panel two-col", children=[
                html.Div([
                    html.Div("3. Your lap", className="step-title"),
                    dash_table.DataTable(
                        id="laps", row_selectable="single", selected_rows=[], page_size=12,
                        columns=[
                            {"name": "Time", "id": "time"},
                            {"name": "Gap", "id": "gap"},
                            {"name": "Lap", "id": "lap"},
                            {"name": "Session", "id": "session"},
                            {"name": "Driver", "id": "driver"},
                        ],
                        style_as_list_view=True,
                        style_cell={"fontFamily": FONT, "fontSize": 14, "padding": "6px 10px",
                                    "textAlign": "left"},
                        style_header={"fontWeight": 600, "backgroundColor": "#f4f5f7"},
                    ),
                    html.Div(id="laps-empty", className="muted mt"),
                ]),
                html.Div([
                    html.Div("4. Compare against", className="step-title"),
                    dcc.RadioItems(id="reference", className="radio-list"),
                    html.Div(id="reference-empty", className="muted"),
                    html.Div(className="row mt", children=[
                        html.Button("Save selected lap as expert lap", id="save-ref",
                                    className="btn"),
                    ]),
                    html.Div(id="save-status", className="muted mt"),
                ]),
            ]),
            dcc.Loading(html.Div(id="results"), type="default"),
        ],
    )

    @app.callback(Output("folder", "value"), Input("browse", "n_clicks"),
                  State("folder", "value"), prevent_initial_call=True)
    def browse(_, current):
        folder = choose_folder(current) if choose_folder else None
        return folder or no_update

    @app.callback(
        Output("recordings", "data"), Output("scan-status", "children"),
        Input("folder", "value"), Input("rescan", "n_clicks"),
    )
    def scan(folder, _):
        folder = (folder or "").strip().strip('"')
        if not folder:
            return [], "Choose the folder LMU saves telemetry to (UserData\\Telemetry)."
        if not Path(folder).is_dir():
            return [], f"Folder not found: {folder}"
        infos, errors = scan_folder(folder)
        if on_folder_scanned:
            on_folder_scanned(folder)
        laps = sum(len(i.laps) for i in infos)
        status = f"{len(infos)} recordings, {laps} timed laps."
        if errors:
            status += f" {len(errors)} could not be read: " + "; ".join(errors[:3])
        return [_info_dict(i) for i in infos], status

    @app.callback(
        Output("track", "options"), Output("track", "value"),
        Input("recordings", "data"), Input("refs-version", "data"), State("track", "value"),
    )
    def tracks(recordings, _, current):
        refs = references()
        names = {r["track"] for r in recordings} | {r.track for r in refs}
        options = []
        for t in sorted(names):
            mine = sum(len(r["laps"]) for r in recordings if r["track"] == t)
            experts = sum(1 for r in refs if r.track == t)
            options.append({"label": f"{t}  ({mine} your laps · {experts} expert)", "value": t})
        if current in names:
            return options, current
        default = recordings[0]["track"] if recordings else (refs[0].track if refs else None)
        return options, default

    @app.callback(
        Output("car", "options"), Output("car", "value"),
        Input("track", "value"), Input("recordings", "data"), Input("refs-version", "data"),
        State("car", "value"),
    )
    def cars(track, recordings, _, current):
        refs = [r for r in references() if r.track == track]
        mine = [r for r in recordings if r["track"] == track]
        names = {r["car"] for r in mine} | {r.car for r in refs}
        options = []
        for c in sorted(names):
            n_mine = sum(len(r["laps"]) for r in mine if r["car"] == c)
            n_ref = sum(1 for r in refs if r.car == c)
            options.append({"label": f"{c}  ({n_mine} your laps · {n_ref} expert)", "value": c})
        if current in names:
            return options, current
        default = mine[0]["car"] if mine else (refs[0].car if refs else None)
        return options, default

    @app.callback(
        Output("laps", "data"), Output("laps", "selected_rows"), Output("laps-empty", "children"),
        Input("track", "value"), Input("car", "value"), Input("recordings", "data"),
        State("laps", "data"), State("laps", "selected_rows"),
    )
    def laps_table(track, car, recordings, old_rows, old_selected):
        rows = _my_laps(recordings, track, car)
        if not rows:
            return [], [], "No laps of yours for this track and car in the telemetry folder."
        best = rows[0]["seconds"]
        for r in rows:
            r["gap"] = "" if r["seconds"] == best else f"+{r['seconds'] - best:.3f}"
        selected = [0]
        if old_rows and old_selected:
            keep = old_rows[old_selected[0]]["id"]
            selected = [i for i, r in enumerate(rows) if r["id"] == keep] or [0]
        return rows, selected, ""

    @app.callback(
        Output("reference", "options"), Output("reference", "value"),
        Output("reference-empty", "children"),
        Input("track", "value"), Input("car", "value"), Input("recordings", "data"),
        Input("refs-version", "data"), State("reference", "value"),
    )
    def reference_options(track, car, recordings, _, current):
        refs = sorted((r for r in references() if r.track == track), key=lambda r: r.lap_time_s)
        same = [r for r in refs if r.car == car]
        car_class = next((r.car_class for r in same), None) or next(
            (r["car_class"] for r in recordings if r["track"] == track and r["car"] == car), None
        )
        other = [r for r in refs if r.car != car and car_class and r.car_class == car_class]
        options = [
            {"label": f"★ {r.driver or 'Unknown'} · {_fmt_time(r.lap_time_s)}", "value": f"ref:{r.path}"}
            for r in same
        ] + [
            {"label": f"{r.driver or 'Unknown'} · {_fmt_time(r.lap_time_s)} · other car: {r.car}",
             "value": f"ref:{r.path}"}
            for r in other
        ]
        mine = _my_laps(recordings, track, car)
        if mine:
            options += [
                {"label": f"Your best lap · {mine[0]['time']}", "value": SELF_BEST},
                {"label": "Your fastest time in each section", "value": SELF_SECTIONS},
            ]
        values = [o["value"] for o in options]
        empty = "" if same else "No expert laps for this car yet."
        if current in values:
            return options, current, empty
        return options, (values[0] if values else None), empty

    @app.callback(
        Output("save-status", "children"), Output("refs-version", "data"),
        Input("save-ref", "n_clicks"),
        State("laps", "data"), State("laps", "selected_rows"), State("refs-version", "data"),
        prevent_initial_call=True,
    )
    def save_expert(_, rows, selected, version):
        if not rows or not selected:
            return "Select one of your laps first.", no_update
        row = rows[selected[0]]
        path = save_reference(load_lap(row["path"], row["lap"]), user_reference_dir)
        return f"Saved to {path}", version + 1

    @app.callback(
        Output("results", "children"),
        Input("laps", "selected_rows"), Input("reference", "value"),
        State("laps", "data"), State("recordings", "data"),
        State("track", "value"), State("car", "value"),
    )
    def results(selected, reference, rows, recordings, track, car):
        if not rows or not selected or not reference:
            return html.P("Pick one of your laps and something to compare it against.",
                          className="note")
        row = rows[selected[0]]
        lap = load_lap(row["path"], row["lap"])
        lap_label = f"Your lap {row['lap']} ({row['time']})"

        if reference == SELF_SECTIONS:
            mine = _my_laps(recordings, track, car)[:MAX_SECTION_LAPS]
            laps = {r["id"]: load_lap(r["path"], r["lap"]) for r in mine}
            laps[row["id"]] = lap
            analysis = analyse(laps)
            best_id = analysis.best_lap
            intro = ("Compared with your fastest time through each section, from any of your "
                     f"{len(laps)} laps here (the reference differs per section). Theoretical "
                     f"best: {_fmt_time(analysis.theoretical_best_s)}. Traces show your best lap.")
            return _results_view(analysis, row["id"], best_id, lap_label,
                                 f"Your best lap ({_fmt_time(analysis.lap_times[best_id])})",
                                 intro)

        if reference == SELF_BEST:
            best = _my_laps(recordings, track, car)[0]
            ref = load_lap(best["path"], best["lap"])
            ref_label = f"Your best lap {best['lap']} ({best['time']})"
        else:
            path = reference.removeprefix("ref:")
            ref = load_reference(path)
            ref_label = f"{ref.facts.driver or 'Expert'} ({_fmt_time(ref.facts.lap_time_s)})"
        if _same_lap(lap, ref):
            return html.P("This lap is the reference itself. Pick another of your laps, or "
                          "compare against your fastest time in each section.", className="note")
        analysis = analyse({"you": lap, "ref": ref}, reference="ref")
        note = ""
        if ref.facts.car != lap.facts.car:
            note = f" Note: the reference is a different car ({ref.facts.car})."
        intro = f"Compared with {ref_label}, section by section.{note}"
        return _results_view(analysis, "you", "ref", lap_label, ref_label, intro)

    # Linking traces and map happens in the browser (_SYNC_JS, in the page head):
    # moving markers and zooming must feel instant and keep the user's view.
    app.clientside_callback(ClientsideFunction("lmu", "onHover"), Output("sync-hover", "data"),
                            Input("traces", "hoverData"), prevent_initial_call=True)
    app.clientside_callback(ClientsideFunction("lmu", "onTracesZoom"), Output("sync-zoom", "data"),
                            Input("traces", "relayoutData"), prevent_initial_call=True)
    app.clientside_callback(ClientsideFunction("lmu", "onFocus"), Output("sync-focus", "data"),
                            Input({"type": "focus", "index": ALL}, "n_clicks"),
                            State("segments", "data"), prevent_initial_call=True)
    app.clientside_callback(ClientsideFunction("lmu", "onReset"), Output("sync-reset", "data"),
                            Input("reset-zoom", "n_clicks"), prevent_initial_call=True)

    return app


def _results_view(a: SessionAnalysis, lap_id: str, ref_id: str, lap_label: str,
                  ref_label: str, intro: str) -> html.Div:
    lap, ref = a.laps[lap_id], a.laps[ref_id]
    lap_t, ref_t = a.lap_times[lap_id], a.lap_times[ref_id]
    findings = a.findings(lap_id)
    gains = [
        (seg, a.stats[a.best_segment_lap[i]][i].time_s - a.stats[lap_id][i].time_s)
        for i, seg in enumerate(a.segments)
    ]
    gains = sorted(((s, g) for s, g in gains if g >= 0.05), key=lambda x: -x[1])

    cards = [
        ("Your lap", _fmt_time(lap_t), lap_label),
        ("Reference", _fmt_time(ref_t), ref_label),
        ("Gap", f"{lap_t - ref_t:+.3f} s", "positive = slower than the reference"),
    ]
    if findings:
        f = findings[0]
        cards.append(("Biggest loss", f.segment.name, f"{f.time_lost_s:.3f} s"))

    geometry = track_geometry(ref, list(a.laps.values()))
    graph_config = {"displaylogo": False}
    viz = [html.Div(className="viz-traces", children=dcc.Graph(
        id="traces", figure=traces_figure(lap, ref, a.segments, lap_label, ref_label, geometry),
        config=graph_config,
    ))]
    if geometry is not None:
        viz.append(html.Div(className="viz-map", children=dcc.Graph(
            id="map", figure=map_figure(geometry, lap, ref, a.segments, lap_label, ref_label),
            config={**graph_config, "scrollZoom": True},
        )))

    children = [
        dcc.Store(id="segments", data=[[s.start_m, s.end_m] for s in a.segments]),
        html.H2("Comparison"),
        html.P(intro, className="muted"),
        html.Div(className="cards", children=[
            html.Div(className="card", children=[
                html.Div(k, className="label"), html.Div(v, className="value"),
                html.Div(sub, className="muted"),
            ]) for k, v, sub in cards
        ]),
        html.Div(className="row mt viz-help", children=[
            html.Span("On the map, scroll to zoom and drag to move. Drag across the traces to "
                      "zoom in; the map follows. Click a section below to jump to it.",
                      className="muted grow-text"),
            html.Button("Reset zoom", id="reset-zoom", className="btn"),
        ]),
        html.Div(id="viz", className="viz", children=viz),
        html.H2("Where you lose time"),
    ]
    if findings:
        total = sum(f.time_lost_s for f in findings)
        children.append(html.P(f"{total:.3f} s lost in total across {len(findings)} sections. "
                               "Click one to see it on the map and traces.", className="muted"))
        children += [_finding_view(f, a.segments.index(f.segment)) for f in findings]
    else:
        children.append(html.P("Nothing significant to gain against this reference.",
                               className="note"))
    if gains:
        children.append(html.P(
            "Faster than the reference in: "
            + ", ".join(f"{s.name} ({g:.2f} s)" for s, g in gains[:6]) + ".",
            className="gains",
        ))
    children += [html.H2("Sections"), _sections_table(a, lap_id)]
    return html.Div(children)


def _finding_view(f: Finding, index: int) -> html.Div:
    return html.Div(
        id={"type": "focus", "index": index}, n_clicks=0, className=f"finding {f.segment.kind}",
        title="Show on the map and traces",
        children=[
            html.Div(className="head", children=[
                html.Span(f.segment.name, className="name"),
                html.Span(f"{f.segment.start_m:.0f}–{f.segment.end_m:.0f} m", className="muted"),
                html.Span(f"−{f.time_lost_s:.3f} s", className="lost"),
            ]),
            html.Ul([html.Li(t) for t in f.advice]),
        ],
    )


def _sections_table(a: SessionAnalysis, lap_id: str) -> dash_table.DataTable:
    rows = []
    for i, seg in enumerate(a.segments):
        mine = a.stats[lap_id][i].time_s
        ref = a.stats[a.best_segment_lap[i]][i].time_s
        rows.append({
            "section": seg.name + (f" ({seg.direction})" if seg.direction else ""),
            "metres": f"{seg.start_m:.0f}–{seg.end_m:.0f}",
            "you": f"{mine:.3f}", "ref": f"{ref:.3f}",
            "delta": round(mine - ref, 3),
        })
    return dash_table.DataTable(
        data=rows,
        columns=[{"name": "Section", "id": "section"}, {"name": "Metres", "id": "metres"},
                 {"name": "You", "id": "you"}, {"name": "Reference", "id": "ref"},
                 {"name": "Δ (s)", "id": "delta", "type": "numeric"}],
        style_as_list_view=True,
        style_cell={"fontFamily": FONT, "fontSize": 14, "padding": "5px 10px"},
        style_header={"fontWeight": 600, "backgroundColor": "#f4f5f7"},
        style_data_conditional=[
            {"if": {"filter_query": "{delta} > 0.1", "column_id": "delta"},
             "backgroundColor": "#fdecea", "color": "#b42318"},
            {"if": {"filter_query": "{delta} < -0.05", "column_id": "delta"},
             "backgroundColor": "#e7f6ec", "color": "#1a7f37"},
        ],
    )


def _my_laps(recordings: list[dict], track: str | None, car: str | None) -> list[dict]:
    rows = []
    for r in recordings:
        if r["track"] != track or r["car"] != car:
            continue
        for number, seconds in r["laps"]:
            rows.append({
                "id": f"{r['path']}|{number}", "path": r["path"], "lap": number,
                "seconds": seconds, "time": _fmt_time(seconds),
                "session": f"{_fmt_recorded(r['recorded_at'])} · {r['session_type'] or ''}",
                "driver": r["driver"] or "",
            })
    return sorted(rows, key=lambda r: r["seconds"])


def _info_dict(info) -> dict:
    return {
        "path": info.path, "track": info.track, "car": info.car, "car_class": info.car_class,
        "session_type": info.session_type, "driver": info.driver,
        "recorded_at": info.recorded_at,
        "laps": [[l.number, l.lap_time_s] for l in info.laps],
    }


def _same_lap(a, b) -> bool:
    fa, fb = a.facts, b.facts
    return (fa.recorded_at, fa.lap_number, fa.driver) == (fb.recorded_at, fb.lap_number, fb.driver) \
        and fa.recorded_at is not None


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"


def _fmt_recorded(value: str | None) -> str:
    # "2026-10-30T12_56_57Z" -> "2026-10-30 12:56"
    if not value:
        return ""
    return value.replace("T", " ").replace("_", ":")[:16]

_SYNC_JS = """
window.dash_clientside = Object.assign({}, window.dash_clientside, {lmu: (function () {
  const none = () => window.dash_clientside.no_update;
  const plot = id => document.querySelector('#' + id + ' .js-plotly-plot');
  // _fullData has the decoded arrays; trace order matches gd.data.
  const find = (gd, meta) => gd && gd._fullData ? gd._fullData.findIndex(t => t.meta === meta) : -1;
  // Lap data is sampled every metre from 0, so distance d is index round(d).
  const at = (arr, d) => Math.min(arr.length - 1, Math.max(0, Math.round(d)));

  function zoomMap(d0, d1) {
    const gd = plot('map');
    if (!gd) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const meta of ['ref-line', 'lap-line']) {
      const i = find(gd, meta);
      if (i < 0) continue;
      const t = gd._fullData[i];
      for (let k = at(t.x, d0); k <= at(t.x, d1); k++) {
        x0 = Math.min(x0, t.x[k]); x1 = Math.max(x1, t.x[k]);
        y0 = Math.min(y0, t.y[k]); y1 = Math.max(y1, t.y[k]);
      }
    }
    if (!isFinite(x0)) return;
    const pad = Math.max(15, 0.15 * Math.max(x1 - x0, y1 - y0));
    Plotly.relayout(gd, {'xaxis.range': [x0 - pad, x1 + pad], 'yaxis.range': [y0 - pad, y1 + pad]});
  }
  function resetMap() {
    const gd = plot('map');
    if (gd) Plotly.relayout(gd, {'xaxis.autorange': true, 'yaxis.autorange': true});
  }

  return {
    // Hovering the traces shows where both cars are on the map.
    onHover(hover) {
      const gd = plot('map');
      if (!gd || !hover || !hover.points || !hover.points.length) return none();
      const d = hover.points[0].x, update = {x: [], y: []}, idx = [];
      for (const [line, pos] of [['ref-line', 'ref-pos'], ['lap-line', 'lap-pos']]) {
        const li = find(gd, line), pi = find(gd, pos);
        if (li < 0 || pi < 0) continue;
        const t = gd._fullData[li], k = at(t.x, d);
        update.x.push([t.x[k]]); update.y.push([t.y[k]]); idx.push(pi);
      }
      if (idx.length) Plotly.restyle(gd, update, idx);
      return none();
    },
    // Zooming the traces to a distance range zooms the map to the same stretch.
    onTracesZoom(rel) {
      if (!rel) return none();
      let r0, r1, reset = false;
      for (const k in rel) {
        let m = k.match(/^xaxis\\d*\\.range\\[([01])\\]$/);
        if (m) { if (m[1] === '0') r0 = rel[k]; else r1 = rel[k]; }
        if (/^xaxis\\d*\\.range$/.test(k)) { r0 = rel[k][0]; r1 = rel[k][1]; }
        if (/^xaxis\\d*\\.autorange$/.test(k)) reset = true;
      }
      if (r0 !== undefined && r1 !== undefined) zoomMap(r0, r1);
      else if (reset) resetMap();
      return none();
    },
    // Clicking a finding zooms traces and map to that section.
    onFocus(clicks, segments) {
      const ctx = window.dash_clientside.callback_context;
      if (!ctx.triggered.length || !ctx.triggered[0].value || !segments) return none();
      const id = JSON.parse(ctx.triggered[0].prop_id.split('.')[0]);
      const [s0, s1] = segments[id.index];
      const traces = plot('traces');
      if (traces) Plotly.relayout(traces, {'xaxis.range': [s0 - 40, s1 + 40]});
      zoomMap(s0, s1);
      document.getElementById('viz').scrollIntoView({behavior: 'smooth', block: 'start'});
      return none();
    },
    onReset() {
      const traces = plot('traces');
      if (traces) Plotly.relayout(traces, {'xaxis.autorange': true});
      resetMap();
      return none();
    },
  };
})()});
"""


_INDEX = """<!DOCTYPE html>
<html>
<head>
{%metas%}
<title>{%title%}</title>
{%favicon%}
{%css%}
<script>{sync_js}</script>
<style>
  body { font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1d2128;
         background: #f4f5f7; margin: 0; }
  .page { max-width: 1400px; margin: 0 auto; padding: 20px 16px 48px; }
  h1 { font-size: 24px; margin: 0 0 2px; }
  h2 { font-size: 19px; margin: 28px 0 6px; }
  .muted { color: #646b77; font-size: 13px; }
  .mt { margin-top: 8px; }
  .panel { background: #fff; border: 1px solid #e2e5ea; border-radius: 10px; padding: 16px;
           margin-top: 16px; }
  .steps, .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  @media (max-width: 900px) { .steps, .two-col { grid-template-columns: 1fr; } }
  .step-title { font-weight: 600; margin-bottom: 8px; }
  .row { display: flex; gap: 8px; align-items: center; }
  .grow { flex: 1; min-width: 0; padding: 7px 10px; border: 1px solid #cfd4db; border-radius: 6px;
          font: inherit; }
  .btn { padding: 7px 14px; border: 1px solid #cfd4db; border-radius: 6px; background: #fff;
         font: inherit; cursor: pointer; white-space: nowrap; }
  .btn:hover { background: #f0f2f5; }
  .radio-list label { display: flex; align-items: center; gap: 8px; padding: 6px 8px;
                      border-radius: 6px; cursor: pointer; }
  .radio-list label:hover { background: #f4f5f7; }
  .radio-list input { margin: 0; }
  #results { background: #fff; border: 1px solid #e2e5ea; border-radius: 10px; padding: 4px 16px 16px;
             margin-top: 16px; }
  .cards { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
  .card { border: 1px solid #e2e5ea; border-radius: 10px; padding: 10px 14px; min-width: 180px; }
  .card .label { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #646b77; }
  .card .value { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .finding { border: 1px solid #e2e5ea; border-left: 4px solid #d6352b; border-radius: 8px;
             padding: 8px 14px; margin: 8px 0; }
  .finding.straight { border-left-color: #e3a008; }
  .finding .head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  .finding .name { font-weight: 600; font-size: 16px; }
  .finding .lost { margin-left: auto; font-weight: 600; color: #b42318; font-variant-numeric: tabular-nums; }
  .finding ul { margin: 4px 0 0; padding-left: 20px; }
  .gains { background: #e7f6ec; border: 1px solid #b7e1c3; padding: 8px 14px; border-radius: 8px; }
  .viz { display: grid; grid-template-columns: 3fr 2fr; gap: 12px; margin-top: 8px;
         align-items: start; }
  /* The map stays in view while scrolling through the traces. */
  .viz-map { position: sticky; top: 8px; border: 1px solid #eceef2; border-radius: 8px; }
  @media (max-width: 1000px) {
    .viz { grid-template-columns: 1fr; }
    .viz-map { position: static; order: -1; }
  }
  .viz-help { justify-content: space-between; }
  .grow-text { flex: 1; }
  .finding { cursor: pointer; }
  .finding:hover { background: #f8f9fb; }
  .note { background: #fff8e6; border: 1px solid #f3dc9b; padding: 10px 14px; border-radius: 8px; }
</style>
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>
"""
