"""LMU Lap Analyser web app (Plotly Dash), run locally by the desktop launcher.

Flow: pick a track and car, point it at your telemetry folder, pick one of
your laps and what to compare it against (an expert lap, your own best lap,
or your fastest time through each section), and read the analysis.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

import flask

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
from webapp.server_client import ServerClient, ServerError

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
SELF_BEST = "self:best"
SELF_SECTIONS = "self:sections"
# Laps used for "your fastest sections"; more adds little and costs time.
MAX_SECTION_LAPS = 30


def create_app(
    reference_dirs: list[Path],
    user_reference_dir: Path,
    initial_folder: str = "",
    choose_folder: Callable[[str | None], str | None] | None = None,
    settings: dict | None = None,
    save_settings: Callable[[dict], None] | None = None,
    cache_dir: Path | None = None,
) -> Dash:
    """``settings`` (telemetry folder, server address and sign-in) is updated in
    place and passed to ``save_settings`` whenever it changes."""
    settings = settings if settings is not None else {}
    initial_folder = initial_folder or settings.get("telemetry_dir", "")
    cache_dir = Path(cache_dir) if cache_dir else user_reference_dir.parent / "cache"
    clients: dict[tuple, ServerClient] = {}

    def remember(**changes) -> None:
        if any(settings.get(k) != v for k, v in changes.items()):
            settings.update(changes)
            if save_settings:
                save_settings(settings)

    def server() -> ServerClient | None:
        """Client for the configured server (kept, so its cache survives), or None."""
        url = (settings.get("server_url") or "").strip()
        if not url:
            return None
        key = (url, settings.get("server_token"))
        if key not in clients:
            clients.clear()
            clients[key] = ServerClient(url, settings.get("server_token"))
        return clients[key]

    def server_boards() -> list[dict]:
        try:
            return server().leaderboards() if server() else []
        except ServerError:
            return []

    def class_of(track: str, car: str, recordings: list[dict]) -> str | None:
        """A car's class, from expert laps, the user's recordings or the leaderboards."""
        return next((r.car_class for r in references() if r.car == car and r.car_class), None) \
            or next((r["car_class"] for r in recordings
                     if r["track"] == track and r["car"] == car and r["car_class"]), None) \
            or next((b["car_class"] for b in server_boards()
                     if b["track"] == track and b["car"] == car and b["car_class"]), None)

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
            dcc.Store(id="server-version", data=0),
            # Outputs for the browser-side callbacks that link traces and map.
            dcc.Store(id="sync-hover"), dcc.Store(id="sync-zoom"),
            dcc.Store(id="sync-focus"), dcc.Store(id="sync-reset"),
            html.Header([
                html.H1("LMU Lap Analyser"),
                html.P("Compare your Le Mans Ultimate laps with faster ones, corner by corner.",
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
            html.Section(className="panel server", children=[
                html.Div("Online leaderboard", className="step-title"),
                html.Div(className="row", children=[
                    html.Div(id="server-status", className="muted grow-text"),
                    html.A("Sign in with Steam", id="sign-in", href="/server-login",
                           className="btn"),
                    html.Button("Sign out", id="sign-out", className="btn"),
                ]),
                # Anyone can see the leaderboard; signing in is only for uploading.
                html.Div(id="board-note", className="mt"),
                html.Div(id="board-wrap", className="mt", children=[
                    dash_table.DataTable(
                        id="board", page_size=15,
                        columns=[
                            {"name": "#", "id": "rank"},
                            {"name": "Driver", "id": "driver"},
                            {"name": "Car", "id": "car"},
                            {"name": "Time", "id": "time"},
                            {"name": "Gap", "id": "gap"},
                            {"name": "Recorded", "id": "recorded"},
                        ],
                        style_as_list_view=True,
                        style_cell={"fontFamily": FONT, "fontSize": 14, "padding": "6px 10px",
                                    "textAlign": "left"},
                        style_header={"fontWeight": 600, "backgroundColor": "#f4f5f7"},
                        # The selected car's laps stand out on the class leaderboard.
                        style_data_conditional=[
                            {"if": {"filter_query": "{selected} = 1"},
                             "backgroundColor": "#eef3fd", "fontWeight": 600},
                        ],
                    ),
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
                    html.Div(className="row mt wrap", children=[
                        html.Button("Upload selected lap to the leaderboard", id="upload",
                                    className="btn primary"),
                        html.Button("Save selected lap as expert lap", id="save-ref",
                                    className="btn"),
                    ]),
                    dcc.Loading(html.Div(id="upload-status", className="mt"), type="dot"),
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
        remember(telemetry_dir=folder)
        laps = sum(len(i.laps) for i in infos)
        status = f"{len(infos)} recordings, {laps} timed laps."
        if errors:
            status += f" {len(errors)} could not be read: " + "; ".join(errors[:3])
        return [_info_dict(i) for i in infos], status

    @app.callback(
        Output("track", "options"), Output("track", "value"),
        Input("recordings", "data"), Input("refs-version", "data"),
        Input("server-version", "data"), State("track", "value"),
    )
    def tracks(recordings, _, __, current):
        refs = references()
        boards = server_boards()
        names = {r["track"] for r in recordings} | {r.track for r in refs} | {b["track"] for b in boards}
        options = []
        for t in sorted(names):
            mine = sum(len(r["laps"]) for r in recordings if r["track"] == t)
            experts = sum(1 for r in refs if r.track == t)
            online = sum(b["laps"] for b in boards if b["track"] == t)
            options.append({"label": f"{t}  ({_lap_counts(mine, experts, online)})", "value": t})
        if current in names:
            return options, current
        default = recordings[0]["track"] if recordings else (refs[0].track if refs else None)
        return options, default

    @app.callback(
        Output("car", "options"), Output("car", "value"),
        Input("track", "value"), Input("recordings", "data"), Input("refs-version", "data"),
        Input("server-version", "data"), State("car", "value"),
    )
    def cars(track, recordings, _, __, current):
        refs = [r for r in references() if r.track == track]
        boards = [b for b in server_boards() if b["track"] == track]
        mine = [r for r in recordings if r["track"] == track]
        names = {r["car"] for r in mine} | {r.car for r in refs} | {b["car"] for b in boards}
        options = []
        for c in sorted(names):
            n_mine = sum(len(r["laps"]) for r in mine if r["car"] == c)
            n_ref = sum(1 for r in refs if r.car == c)
            n_online = sum(b["laps"] for b in boards if b["car"] == c)
            options.append({"label": f"{c}  ({_lap_counts(n_mine, n_ref, n_online)})", "value": c})
        if current in names:
            return options, current
        default = mine[0]["car"] if mine else (
            refs[0].car if refs else (boards[0]["car"] if boards else None))
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
        Input("refs-version", "data"), Input("server-version", "data"),
        State("reference", "value"),
    )
    def reference_options(track, car, recordings, _, __, current):
        online, online_note = [], ""
        if server() and track and car:
            try:
                online = server().leaderboard(track, car)
            except ServerError as exc:
                online_note = f" Online leaderboard: {exc}"
        refs = sorted((r for r in references() if r.track == track), key=lambda r: r.lap_time_s)
        same = [r for r in refs if r.car == car]
        boards = [b for b in server_boards() if b["track"] == track]
        car_class = class_of(track, car, recordings)
        other = [r for r in refs if r.car != car and car_class and r.car_class == car_class]
        # Other cars of the same class: their fastest online lap each.
        other_online = []
        for b in boards:
            if b["car"] != car and car_class and b["car_class"] == car_class:
                try:
                    other_online += [(b["car"], e) for e in server().leaderboard(track, b["car"])[:1]]
                except ServerError:
                    pass
        other_online.sort(key=lambda x: x[1]["lap_time_s"])
        options = [
            {"label": f"#{e['rank']} {e['driver']} · {_fmt_time(e['lap_time_s'])} (online)",
             "value": f"srv:{e['id']}"}
            for e in online
        ] + [
            {"label": f"★ {r.driver or 'Unknown'} · {_fmt_time(r.lap_time_s)}", "value": f"ref:{r.path}"}
            for r in same
        ] + [
            {"label": f"{r.driver or 'Unknown'} · {_fmt_time(r.lap_time_s)} · other car: {r.car}",
             "value": f"ref:{r.path}"}
            for r in other
        ] + [
            {"label": f"#{e['rank']} {e['driver']} · {_fmt_time(e['lap_time_s'])} · other car: "
                      f"{other_car} (online)",
             "value": f"srv:{e['id']}"}
            for other_car, e in other_online
        ]
        mine = _my_laps(recordings, track, car)
        if mine:
            options += [
                {"label": f"Your best lap · {mine[0]['time']}", "value": SELF_BEST},
                {"label": "Your fastest time in each section", "value": SELF_SECTIONS},
            ]
        values = [o["value"] for o in options]
        empty = ("" if same or online else "No other drivers' laps for this car yet.") + online_note
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
        elif reference.startswith("srv:"):
            try:
                path = server().download_lap(reference.removeprefix("srv:"), cache_dir)
            except (ServerError, AttributeError) as exc:
                return html.P(f"Couldn't get that lap from the server: {exc}", className="note")
            ref = load_reference(path)
            ref_label = f"{ref.facts.driver or 'Online'} ({_fmt_time(ref.facts.lap_time_s)})"
        else:
            path = reference.removeprefix("ref:")
            ref = load_reference(path)
            ref_label = f"{ref.facts.driver or 'Expert'} ({_fmt_time(ref.facts.lap_time_s)})"
        if _same_lap(lap, ref):
            return html.P("This lap is the reference itself. Pick another of your laps, or "
                          "compare against your fastest time in each section.", className="note")
        analysis = analyse({"you": lap, "ref": ref}, reference="ref")
        warning = ""
        if ref.facts.car != lap.facts.car:
            same_class = f" (both {lap.facts.car_class})" if lap.facts.car_class else ""
            warning = (f"Different car: the reference is the {ref.facts.car}, yours is the "
                       f"{lap.facts.car}{same_class}. Part of the gap may be the car, not "
                       "the driving.")
        intro = f"Compared with {ref_label}, section by section."
        return _results_view(analysis, "you", "ref", lap_label, ref_label, intro, warning)

    # --- Online leaderboard ----------------------------------------------------

    login_states: set[str] = set()

    @app.server.route("/server-login")
    def server_login():
        if not server():
            return flask.redirect("/")
        state = secrets.token_urlsafe(16)
        login_states.add(state)
        return_to = flask.request.host_url + "server-callback?" + urlencode({"state": state})
        # For local testing against a server started with LMU_DEV_LOGIN=1.
        dev_name = os.environ.get("LMU_DEV_LOGIN_NAME")
        if dev_name:
            query = urlencode({"name": dev_name, "return_to": return_to})
            return flask.redirect(f"{server().url}/auth/dev?{query}")
        return flask.redirect(f"{server().url}/auth/steam/login?" + urlencode({"return_to": return_to}))

    @app.server.route("/server-callback")
    def server_callback():
        # Only accept a token for a sign-in this app started, so another page
        # can't sign the app into someone else's account.
        state = flask.request.args.get("state", "")
        token = flask.request.args.get("token")
        if state in login_states and token:
            login_states.discard(state)
            remember(server_token=token, server_name=flask.request.args.get("name", ""))
        return flask.redirect("/")

    @app.callback(
        Output("server-status", "children"), Output("sign-in", "style"),
        Output("sign-out", "style"),
        Input("server-version", "data"),
    )
    def server_status(_):
        hide, show = {"display": "none"}, {}
        if not server():
            return "No leaderboard server set up.", hide, hide
        try:
            health = server().health()
        except ServerError as exc:
            return str(exc), hide, hide
        rules = f"Ruleset: {health.get('ruleset')}."
        if not settings.get("server_token"):
            return f"Connected. Sign in to upload laps. {rules}", show, hide
        try:
            me = server().me()
        except ServerError as exc:
            return f"{exc} {rules}", show, hide
        return f"Signed in as {me['name']}. {rules}", hide, show

    @app.callback(
        Output("board", "data"), Output("board-wrap", "style"), Output("board-note", "children"),
        Input("track", "value"), Input("car", "value"), Input("recordings", "data"),
        Input("server-version", "data"),
    )
    def board(track, car, recordings, _):
        """The leaderboard for the car's class at this track (or just the car's, if its
        class is unknown), with the selected car's laps highlighted."""
        hide = {"display": "none"}
        if not server() or not track or not car:
            return [], hide, ""
        car_class = class_of(track, car, recordings)
        try:
            entries = (server().class_leaderboard(track, car_class) if car_class
                       else server().leaderboard(track, car))
        except ServerError as exc:
            return [], hide, html.Span(f"Couldn't load the leaderboard: {exc}", className="muted")
        if not entries:
            what = f"{car_class} cars" if car_class else "this car"
            return [], hide, html.Span(f"No laps on the leaderboard for {what} at this track yet.",
                                       className="muted")
        best = entries[0]["lap_time_s"]
        rows = [{
            "rank": i + 1, "driver": e["driver"], "car": e.get("car", car),
            "time": _fmt_time(e["lap_time_s"]),
            "gap": "" if i == 0 else f"+{e['lap_time_s'] - best:.3f}",
            "recorded": _fmt_recorded(e["recorded_at"]),
            "selected": int(e.get("car", car) == car),
        } for i, e in enumerate(entries)]
        title = f"{car_class} at {track}: each car's top drivers" if car_class else \
            f"Top laps: {track} · {car}"
        return rows, {}, html.B(title)

    @app.callback(
        Output("server-version", "data", allow_duplicate=True),
        Input("sign-out", "n_clicks"), State("server-version", "data"),
        prevent_initial_call=True,
    )
    def sign_out(_, version):
        remember(server_token=None, server_name=None)
        return version + 1

    @app.callback(
        Output("upload-status", "children"),
        Output("server-version", "data", allow_duplicate=True),
        Input("upload", "n_clicks"),
        State("laps", "data"), State("laps", "selected_rows"), State("server-version", "data"),
        prevent_initial_call=True,
    )
    def upload(_, rows, selected, version):
        if not rows or not selected:
            return html.P("Select one of your laps first.", className="muted"), no_update
        if not server():
            return html.P("No leaderboard server set up.", className="muted"), no_update
        row = rows[selected[0]]
        try:
            result = server().upload_lap(row["path"], row["lap"])
        except ServerError as exc:
            return html.P(str(exc), className="note"), no_update
        return _upload_view(result, row), version + 1

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


def _upload_view(result: dict, row: dict) -> html.Div:
    """The server's verdict on an upload, with the reasons if it was turned down."""
    if result.get("accepted"):
        return html.Div(className="gains", children=result.get("message", "Uploaded."))
    # The reason already covers rule problems; list failed checks (tampering, gaps).
    failed = [c for c in result.get("checks", []) if not c["ok"]]
    details = [html.Li(f"{c['name']}: {c['detail']}") for c in failed]
    return html.Div(className="note", children=[
        html.Div(f"Lap {row['lap']} ({row['time']}): {result.get('reason', '')}"),
        html.Ul(details) if details else None,
    ])


def _results_view(a: SessionAnalysis, lap_id: str, ref_id: str, lap_label: str,
                  ref_label: str, intro: str, warning: str = "") -> html.Div:
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
    # No Plotly toolbar: zoom by dragging (traces) or scrolling (map), move by
    # dragging the map, reset by double-clicking or with "Reset zoom".
    graph_config = {"displayModeBar": False, "doubleClick": "reset"}
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
        *([html.P(warning, className="note")] if warning else []),
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
             "backgroundColor": "#fdeee6", "color": "#a8461b"},
            {"if": {"filter_query": "{delta} < -0.05", "column_id": "delta"},
             "backgroundColor": "#e3f5ee", "color": "#0b6b4a"},
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


def _lap_counts(mine: int, experts: int, online: int) -> str:
    """For the track and car lists: saved expert laps and leaderboard laps apart,
    so the count matches what the leaderboard shows."""
    parts = [f"{mine} your laps", f"{experts} expert" if experts else "",
             f"{online} on the leaderboard" if online else ""]
    return " · ".join(p for p in parts if p)


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
  a.btn { color: inherit; text-decoration: none; display: inline-block; }
  .btn.primary { background: #1f6fd1; border-color: #1f6fd1; color: #fff; }
  .btn.primary:hover { background: #1a5fb4; }
  .wrap { flex-wrap: wrap; }
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
  /* Orange = time lost, teal = time gained: the same colours as the map. */
  .finding { border: 1px solid #e2e5ea; border-left: 4px solid #eb6834; border-radius: 8px;
             padding: 8px 14px; margin: 8px 0; }
  .finding.straight { border-left-style: dashed; }
  .finding .head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  .finding .name { font-weight: 600; font-size: 16px; }
  .finding .lost { margin-left: auto; font-weight: 600; color: #a8461b; font-variant-numeric: tabular-nums; }
  .finding ul { margin: 4px 0 0; padding-left: 20px; }
  .gains { background: #e3f5ee; border: 1px solid #9fd9c2; padding: 8px 14px; border-radius: 8px; }
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
