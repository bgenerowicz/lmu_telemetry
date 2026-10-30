"""Plot one lap against a reference lap as a standalone HTML page.

    python scripts/compare_laps.py RECORDING.duckdb [--lap N] [--ref N] [-o out.html]

By default compares the second-fastest flying lap against the fastest.
Needs plotly (pip install -e ".[webapp]").
"""

from __future__ import annotations

import argparse

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from lmu_core.compare import delta_time
from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.resample import resample_lap

TRACES = [
    ("Δt to ref (s)", None),
    ("Speed (km/h)", "speed_kph"),
    ("Throttle (%)", "throttle_pct"),
    ("Brake (%)", "brake_pct"),
    ("Steering (%)", "steering_pct"),
    ("Gear", "gear"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording")
    parser.add_argument("--lap", type=int, help="lap number to analyse")
    parser.add_argument("--ref", type=int, help="reference lap number")
    parser.add_argument("-o", "--output", default="compare.html")
    args = parser.parse_args()

    with Recording(args.recording) as rec:
        flying = sorted((l for l in split_laps(rec) if l.is_flying), key=lambda l: l.lap_time_s)
        by_number = {l.number: l for l in flying}
        if len(flying) < 2 and (args.lap is None or args.ref is None):
            raise SystemExit("need at least two flying laps")
        ref_lap = by_number[args.ref] if args.ref is not None else flying[0]
        lap_lap = by_number[args.lap] if args.lap is not None else flying[1]
        ref = resample_lap(rec, ref_lap)
        lap = resample_lap(rec, lap_lap)

    lap_name = f"Lap {lap_lap.number} ({lap_lap.lap_time_s:.3f})"
    ref_name = f"Lap {ref_lap.number} ({ref_lap.lap_time_s:.3f})"
    colors = {lap_name: "#d62728", ref_name: "#1f77b4"}

    fig = make_subplots(
        rows=len(TRACES), cols=2, shared_xaxes=True, vertical_spacing=0.02,
        column_widths=[0.68, 0.32], specs=[[{}, {"rowspan": len(TRACES)}]]
        + [[{}, None]] * (len(TRACES) - 1),
        subplot_titles=[TRACES[0][0], "Track map (colour = Δt gained/lost)"]
        + [t for t, _ in TRACES[1:]],
    )
    d, dt = delta_time(lap, ref)
    fig.add_trace(go.Scatter(x=d, y=dt, name="Δt", line_color="black", showlegend=False), 1, 1)
    for row, (_, column) in enumerate(TRACES[1:], start=2):
        for name, p in ((ref_name, ref), (lap_name, lap)):
            fig.add_trace(
                go.Scatter(
                    x=p.distance_m, y=p.data[column], name=name, legendgroup=name,
                    showlegend=row == 2, line=dict(color=colors[name], width=1.5),
                ),
                row, 1,
            )
    # Where time is lost: slope of Δt over the next 50 m.
    window = 50
    lost = dt[window:] - dt[:-window]
    fig.add_trace(
        go.Scatter(
            x=ref.data["x_m"][:-window], y=ref.data["y_m"][:-window], mode="markers",
            marker=dict(color=lost, colorscale="RdBu_r", cmid=0, size=4,
                        colorbar=dict(title="s / 50 m", x=1.02)),
            text=[f"{x:.0f} m" for x in d[:-window]], hoverinfo="text", showlegend=False,
        ),
        1, 2,
    )
    fig.update_xaxes(title_text="Distance (m)", row=len(TRACES), col=1)
    fig.update_yaxes(scaleanchor="x2", scaleratio=1, row=1, col=2, visible=False)
    fig.update_xaxes(visible=False, row=1, col=2)
    fig.update_layout(
        title=f"{lap.facts.track} · {lap.facts.car}: {lap_name} vs {ref_name}",
        height=1100, hovermode="x unified",
        legend=dict(orientation="h", x=0, y=1.04, xanchor="left", yanchor="bottom"),
    )
    fig.write_html(args.output, include_plotlyjs=True)
    print(f"wrote {args.output}  (final Δt {dt[-1]:+.3f} s)")


if __name__ == "__main__":
    main()
