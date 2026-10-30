"""Plotly figure comparing one lap with a reference lap."""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from lmu_core.resample import ProcessedLap
from lmu_core.segments import Segment

ROWS = [
    ("Δt to reference (s)", None),
    ("Speed (km/h)", "speed_kph"),
    ("Throttle (%)", "throttle_pct"),
    ("Brake (%)", "brake_pct"),
    ("Steering (%)", "steering_pct"),
    ("Gear", "gear"),
]
REF_COLOR = "#1f6fd1"
LAP_COLOR = "#d6352b"
# Window over which the map shows time gained or lost.
MAP_WINDOW_M = 50


def comparison_figure(
    lap: ProcessedLap, ref: ProcessedLap, segments: list[Segment], lap_name: str, ref_name: str
) -> go.Figure:
    """Both laps must be on the same distance grid (as in a SessionAnalysis)."""
    n_rows = len(ROWS)
    fig = make_subplots(
        rows=n_rows, cols=2, shared_xaxes=True, vertical_spacing=0.025,
        column_widths=[0.66, 0.34],
        specs=[[{}, {"rowspan": n_rows}]] + [[{}, None]] * (n_rows - 1),
        # The first chart's top edge carries corner labels, so it is titled on its axis.
        subplot_titles=["", "Track map"] + [t for t, _ in ROWS[1:]],
    )
    d = ref.distance_m
    dt = lap.time_s - ref.time_s
    fig.add_trace(go.Scatter(x=d, y=dt, name="Δt", line=dict(color="#222", width=1.5),
                             showlegend=False), 1, 1)
    for row, (_, col) in enumerate(ROWS[1:], start=2):
        for name, p, color in ((ref_name, ref, REF_COLOR), (lap_name, lap, LAP_COLOR)):
            if col in p.data:
                fig.add_trace(
                    go.Scatter(x=d, y=p.data[col], name=name, legendgroup=name,
                               showlegend=row == 2, line=dict(color=color, width=1.4)),
                    row, 1,
                )

    if "x_m" in ref.data:
        x, y = ref.data["x_m"], ref.data["y_m"]
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(color="#c8ccd2", width=9),
                                 hoverinfo="skip", showlegend=False), 1, 2)
        w = MAP_WINDOW_M
        lost = dt[w:] - dt[:-w]
        fig.add_trace(
            go.Scatter(
                x=x[:-w], y=y[:-w], mode="markers",
                marker=dict(color=lost, colorscale="RdBu_r", cmid=0, size=5,
                            colorbar=dict(title=f"s lost<br>per {w} m", x=1.0, len=0.5, y=0.75)),
                text=[f"{v:.0f} m: {t:+.3f} s" for v, t in zip(d[:-w], lost)],
                hoverinfo="text", showlegend=False,
            ),
            1, 2,
        )
        for s in segments:
            if s.kind == "corner" and s.apex_m is not None:
                i = int(np.searchsorted(d, s.apex_m))
                fig.add_annotation(x=x[i], y=y[i], text=s.name, showarrow=False,
                                   font=dict(size=11, color="#1d2128"),
                                   bgcolor="rgba(255,255,255,0.85)", bordercolor="#9aa1ac",
                                   borderpad=2, row=1, col=2)

    for s in segments:
        if s.kind == "corner":
            fig.add_vrect(x0=s.start_m, x1=s.end_m, row="all", col=1, fillcolor="#8a94a6",
                          opacity=0.10, line_width=0)
            fig.add_annotation(x=(s.start_m + s.end_m) / 2, y=1.0, yref="y domain", text=s.name,
                               showarrow=False, yanchor="bottom", font=dict(size=10), row=1, col=1)

    fig.update_xaxes(title_text="Distance (m)", row=n_rows, col=1)
    fig.update_yaxes(title_text=ROWS[0][0], row=1, col=1)
    fig.update_xaxes(visible=False, row=1, col=2)
    fig.update_yaxes(visible=False, scaleanchor="x2", scaleratio=1, row=1, col=2)
    fig.update_layout(
        height=1000, hovermode="x unified", margin=dict(l=50, r=20, t=50, b=40),
        legend=dict(orientation="h", x=0, y=1.03, xanchor="left", yanchor="bottom"),
        plot_bgcolor="#f7f8fa", paper_bgcolor="white",
    )
    return fig
