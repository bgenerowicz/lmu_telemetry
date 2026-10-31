"""Plotly figures comparing one lap with a reference lap.

Two figures, linked in the browser (see ``_SYNC_JS`` in app.py):

- traces against distance (time gap, speed, pedals, steering, line, rpm, gear);
- a map with both driving lines, the start/finish line and sectors, which
  pans and zooms on its own.

Map traces carry a ``meta`` tag so the browser code can find them.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from lmu_core.geometry import TrackGeometry, sector_starts
from lmu_core.resample import ProcessedLap
from lmu_core.segments import Segment

ROWS = [
    ("Δt to reference (s)", None),
    ("Speed (km/h)", "speed_kph"),
    ("Throttle (%)", "throttle_pct"),
    ("Brake (%)", "brake_pct"),
    ("Steering (%)", "steering_pct"),
    ("Line: lateral position (m, + = right)", "path_lateral_m"),
    ("RPM", "rpm"),
    ("Gear", "gear"),
]
# Colours have one job each. Which lap: the reference is a neutral "ghost"
# grey and your lap the one strong colour. Time lost/gained: orange/teal with a
# grey middle, used for nothing else (map band, finding cards, sections table).
# Checked with the dataviz palette validator, including colour-blind separation.
REF_COLOR = "#52514e"
LAP_COLOR = "#6250d6"
LOSS_COLOR = "#eb6834"
GAIN_COLOR = "#1baf7a"
EDGE_COLOR = "#9aa1ac"
# Gain -> nothing -> loss; the middle matches the track surface, so sections
# with no difference disappear into it.
DELTA_SCALE = [[0.0, GAIN_COLOR], [0.5, "#e4e7ec"], [1.0, LOSS_COLOR]]
# Window over which the map shows time gained or lost.
MAP_WINDOW_M = 50
# Start/finish and sector lines run across the track: past each edge by this
# much, or this far either side of the reference path where edges weren't
# recorded.
LINE_OVERHANG_M = 4.0
LINE_HALF_WIDTH_M = 10.0


def traces_figure(
    lap: ProcessedLap, ref: ProcessedLap, segments: list[Segment], lap_name: str, ref_name: str,
    geometry: TrackGeometry | None = None,
) -> go.Figure:
    """Both laps must be on the same distance grid (as in a SessionAnalysis)."""
    rows = [r for r in ROWS if r[1] is None or r[1] in ref.data]
    n_rows = len(rows)
    fig = make_subplots(
        rows=n_rows, cols=1, shared_xaxes=True, vertical_spacing=0.022,
        # The first chart's top edge carries corner labels, so it is titled on its axis.
        subplot_titles=[""] + [t for t, _ in rows[1:]],
    )
    d = ref.distance_m
    fig.add_trace(go.Scatter(x=d, y=lap.time_s - ref.time_s, name="Δt",
                             line=dict(color="#222", width=1.5), showlegend=False), 1, 1)
    for row, (_, col) in enumerate(rows[1:], start=2):
        if col == "path_lateral_m" and geometry is not None and geometry.left_edge_m is not None:
            for edge in (geometry.left_edge_m, geometry.right_edge_m):
                fig.add_trace(go.Scatter(x=d, y=edge, name="Track edge", hoverinfo="skip",
                                         line=dict(color=EDGE_COLOR, width=1, dash="dot"),
                                         showlegend=False), row, 1)
        for name, p, color in ((ref_name, ref, REF_COLOR), (lap_name, lap, LAP_COLOR)):
            if col in p.data:
                fig.add_trace(
                    go.Scatter(x=d, y=p.data[col], name=name, legendgroup=name,
                               showlegend=row == 2, line=dict(color=color, width=1.4)),
                    row, 1,
                )
    for s in segments:
        if s.kind == "corner":
            fig.add_vrect(x0=s.start_m, x1=s.end_m, row="all", col=1, fillcolor="#8a94a6",
                          opacity=0.10, line_width=0)
            fig.add_annotation(x=(s.start_m + s.end_m) / 2, y=1.0, yref="y domain", text=s.name,
                               showarrow=False, yanchor="bottom", font=dict(size=10), row=1, col=1)

    fig.update_xaxes(title_text="Distance (m)", row=n_rows, col=1)
    fig.update_yaxes(title_text="Δt (s)", row=1, col=1)
    fig.update_layout(
        height=130 * n_rows + 40, hovermode="x unified", margin=dict(l=50, r=10, t=40, b=40),
        legend=dict(orientation="h", x=0, y=1.02, xanchor="left", yanchor="bottom"),
        plot_bgcolor="#f7f8fa", paper_bgcolor="white", dragmode="zoom",
        uirevision="traces",
    )
    return fig


def map_figure(
    geometry: TrackGeometry, lap: ProcessedLap, ref: ProcessedLap, segments: list[Segment],
    lap_name: str, ref_name: str,
) -> go.Figure:
    g = geometry
    d = g.distance_m
    fig = go.Figure()

    if g.left_edge_m is not None:
        lx, ly = g.at(g.left_edge_m)
        rx, ry = g.at(g.right_edge_m)
        fig.add_trace(go.Scatter(
            x=np.concatenate([lx, rx[::-1]]), y=np.concatenate([ly, ry[::-1]]),
            fill="toself", fillcolor="#e4e7ec", line=dict(width=0), hoverinfo="skip",
            showlegend=False,
        ))
        for ex, ey in ((lx, ly), (rx, ry)):
            fig.add_trace(go.Scatter(x=ex, y=ey, mode="lines", hoverinfo="skip", showlegend=False,
                                     line=dict(color=EDGE_COLOR, width=1)))
    else:
        fig.add_trace(go.Scatter(x=g.centre_x, y=g.centre_y, mode="lines", hoverinfo="skip",
                                 line=dict(color="#e4e7ec", width=14), showlegend=False))

    ref_x, ref_y = g.line(ref)
    lap_x, lap_y = g.line(lap)
    dt = lap.time_s - ref.time_s
    w = MAP_WINDOW_M
    lost = np.concatenate([dt[w:] - dt[:-w], np.zeros(w)])
    fig.add_trace(go.Scatter(
        x=ref_x, y=ref_y, mode="markers", name="Time lost", meta="loss",
        marker=dict(color=lost, colorscale=DELTA_SCALE, cmid=0, size=12, opacity=0.75,
                    colorbar=dict(title=f"per {w} m", len=0.35, y=0.85, thickness=12,
                                  ticksuffix=" s")),
        hoverinfo="skip", showlegend=False,
    ))
    for name, x, y, color, meta in ((ref_name, ref_x, ref_y, REF_COLOR, "ref-line"),
                                    (lap_name, lap_x, lap_y, LAP_COLOR, "lap-line")):
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="lines", name=name, meta=meta, line=dict(color=color, width=2),
            customdata=d, hovertemplate="%{customdata:.0f} m<extra>" + name + "</extra>",
        ))
    for color, meta in ((REF_COLOR, "ref-pos"), (LAP_COLOR, "lap-pos")):
        fig.add_trace(go.Scatter(
            x=[], y=[], mode="markers", meta=meta, hoverinfo="skip", showlegend=False,
            marker=dict(color=color, size=11, line=dict(color="white", width=2)),
        ))

    marks = [(0.0, "Start/finish", "solid")] + [
        (at, f"Sector {n}", "dash") for n, at in enumerate(sector_starts([lap, ref]), start=2)]
    for at, text, dash in marks:
        i = min(int(np.searchsorted(d, at)), len(d) - 1)
        left, right = -LINE_HALF_WIDTH_M, LINE_HALF_WIDTH_M
        if g.left_edge_m is not None:
            left, right = g.left_edge_m[i] - LINE_OVERHANG_M, g.right_edge_m[i] + LINE_OVERHANG_M
        across = np.array([left, right])
        x, y = g.centre_x[i] + across * g.normal_x[i], g.centre_y[i] + across * g.normal_y[i]
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", hoverinfo="skip", showlegend=False,
                                 line=dict(color="#1d2128", width=3, dash=dash)))
        fig.add_annotation(x=x[1], y=y[1], text=text, showarrow=True, arrowwidth=1,
                           arrowcolor="#1d2128", ax=18, ay=-18, font=dict(size=11, color="#1d2128"),
                           bgcolor="rgba(255,255,255,0.85)", borderpad=2)

    for s in segments:
        if s.kind == "corner" and s.apex_m is not None:
            i = int(np.searchsorted(d, s.apex_m))
            fig.add_annotation(x=g.centre_x[i], y=g.centre_y[i], text=s.name, showarrow=False,
                               font=dict(size=11, color="#1d2128"),
                               bgcolor="rgba(255,255,255,0.85)", bordercolor=EDGE_COLOR,
                               borderpad=2)

    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1)
    fig.update_layout(
        height=560, margin=dict(l=0, r=0, t=40, b=0), dragmode="pan",
        legend=dict(orientation="h", x=0, y=1.0, xanchor="left", yanchor="bottom"),
        plot_bgcolor="white", paper_bgcolor="white", hovermode="closest",
        uirevision="map",
    )
    return fig
