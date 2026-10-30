"""The HTML report: a self-contained page that works offline."""

from __future__ import annotations

import html
from datetime import datetime

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from lmu_core.analysis import Finding, SessionAnalysis

CHART_ROWS = [
    ("Δt to best lap (s)", None),
    ("Speed (km/h)", "speed_kph"),
    ("Throttle (%)", "throttle_pct"),
    ("Brake (%)", "brake_pct"),
    ("Steering (%)", "steering_pct"),
    ("Gear", "gear"),
]
BEST_COLOR = "#1f6fd1"
LAP_COLOR = "#d6352b"
# Window over which the map shows time gained or lost.
MAP_WINDOW_M = 50


def render(analysis: SessionAnalysis, sources: list[str]) -> str:
    a = analysis
    facts = a.laps[a.best_lap].facts
    times = a.lap_times
    best_t = times[a.best_lap]
    gain = best_t - a.theoretical_best_s
    spreads = [a.segment_spread_s(i) for i in range(len(a.segments))]
    worst = int(np.argmax(spreads)) if len(a.laps) > 1 else None

    cards = [
        ("Best lap", _fmt_time(best_t), f"lap {a.best_lap} of {len(a.laps)} timed"),
        ("Theoretical best", _fmt_time(a.theoretical_best_s), f"{gain:.3f} s from your best segments"),
    ]
    if worst is not None:
        cards.append(
            ("Least consistent", a.segments[worst].name, f"{spreads[worst]:.3f} s between laps")
        )

    title = f"{facts.track} · {facts.car}"
    parts = [
        f"<header><h1>{_e(title)}</h1><p class=muted>{_e(facts.car_class or '')} · "
        f"{_e(facts.session_type or '')} · {_e(facts.weather or '')} · "
        f"{len(a.laps)} timed laps · {_e(', '.join(sources))}</p></header>",
        '<section class=cards>'
        + "".join(
            f"<div class=card><div class=label>{_e(k)}</div><div class=value>{_e(v)}</div>"
            f"<div class=muted>{_e(sub)}</div></div>"
            for k, v, sub in cards
        )
        + "</section>",
    ]

    if len(a.laps) < 2:
        parts.append(
            "<p class=note>Only one timed lap, so there is nothing to compare yet. "
            "Drive a few more laps in one session and run this again.</p>"
        )
    else:
        parts.append(
            "<h2>Where your best lap loses time</h2><p class=muted>Compared with your "
            "fastest time through each section, from any lap.</p>"
            + _findings_html(a.findings(a.best_lap))
        )
    parts.append("<h2>Compare laps</h2>" + _chart_html(a))
    parts.append("<h2>Sections</h2>" + _segments_table(a, spreads))
    others = sorted((n for n in a.laps if n != a.best_lap), key=times.get)
    if others:
        parts.append("<h2>Other laps</h2>")
        for n in others:
            parts.append(
                f"<details><summary>Lap {_e(n)} · {_fmt_time(times[n])} "
                f"(+{times[n] - best_t:.3f})</summary>{_findings_html(a.findings(n))}</details>"
            )
    parts.append(
        f"<footer class=muted>Generated {datetime.now():%Y-%m-%d %H:%M}. Corner numbers "
        "count detected corners and may not match the track's official turn numbers.</footer>"
    )
    return _PAGE.format(title=_e(title), body="\n".join(parts))


def _findings_html(findings: list[Finding]) -> str:
    if not findings:
        return "<p class=muted>Nothing significant: this lap was the fastest or close in every section.</p>"
    total = sum(f.time_lost_s for f in findings)
    rows = []
    for f in findings:
        tips = "".join(f"<li>{_e(t)}</li>" for t in f.advice)
        kind = "corner" if f.segment.kind == "corner" else "straight"
        rows.append(
            f"<div class='finding {kind}'><div class=head><span class=name>{_e(f.segment.name)}</span>"
            f"<span class=muted>{f.segment.start_m:.0f}–{f.segment.end_m:.0f} m · vs lap "
            f"{_e(f.ref_lap)}</span><span class=lost>−{f.time_lost_s:.3f} s</span></div>"
            f"<ul>{tips}</ul></div>"
        )
    return f"<p class=muted>{total:.3f} s in total.</p>" + "".join(rows)


def _segments_table(a: SessionAnalysis, spreads: list[float]) -> str:
    laps = sorted(a.laps, key=a.lap_times.get)
    head = "".join(f"<th>Lap {_e(n)}</th>" for n in laps)
    rows = []
    for i, seg in enumerate(a.segments):
        best = a.stats[a.best_segment_lap[i]][i].time_s
        cells = []
        for n in laps:
            t = a.stats[n][i].time_s
            d = t - best
            cls = "best" if d < 1e-6 else ("bad" if d > 0.2 else "")
            cells.append(f"<td class='{cls}'>{t:.3f}" + (f"<small> +{d:.3f}</small>" if d > 1e-6 else "") + "</td>")
        direction = f" ({seg.direction})" if seg.direction else ""
        rows.append(
            f"<tr><td>{_e(seg.name)}{direction}</td><td>{seg.start_m:.0f}–{seg.end_m:.0f}</td>"
            + "".join(cells)
            + f"<td>{spreads[i]:.3f}</td></tr>"
        )
    return (
        "<div class=scroll><table><thead><tr><th>Section</th><th>Metres</th>"
        f"{head}<th>Spread</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        "<p class=muted>Times per section; green is the fastest, red more than 0.2 s off it. "
        "Spread is slowest minus fastest: large spreads are where you are inconsistent.</p>"
    )


def _chart_html(a: SessionAnalysis) -> str:
    best = a.laps[a.best_lap]
    others = sorted((n for n in a.laps if n != a.best_lap), key=a.lap_times.get)
    n_rows = len(CHART_ROWS)
    fig = make_subplots(
        rows=n_rows, cols=2, shared_xaxes=True, vertical_spacing=0.025,
        column_widths=[0.66, 0.34],
        specs=[[{}, {"rowspan": n_rows}]] + [[{}, None]] * (n_rows - 1),
        # The first chart's top edge carries corner labels, so it is titled on its axis.
        subplot_titles=["", "Track map"] + [t for t, _ in CHART_ROWS[1:]],
    )
    d = best.distance_m
    best_name = f"Best: lap {a.best_lap} ({_fmt_time(a.lap_times[a.best_lap])})"

    # Always-visible traces: the best lap and the outline of the track.
    fixed = 0
    for row, (_, col) in enumerate(CHART_ROWS[1:], start=2):
        fig.add_trace(
            go.Scatter(x=d, y=best.data[col], name=best_name, legendgroup="best",
                       showlegend=row == 2, line=dict(color=BEST_COLOR, width=1.4)),
            row, 1,
        )
        fixed += 1
    fig.add_trace(
        go.Scatter(x=best.data["x_m"], y=best.data["y_m"], mode="lines",
                   line=dict(color="#c8ccd2", width=9), hoverinfo="skip", showlegend=False),
        1, 2,
    )
    for s in a.segments:
        if s.kind == "corner" and s.apex_m is not None:
            i = int(np.searchsorted(d, s.apex_m))
            fig.add_annotation(x=best.data["x_m"][i], y=best.data["y_m"][i], text=s.name,
                               showarrow=False, font=dict(size=11, color="#1d2128"),
                               bgcolor="rgba(255,255,255,0.85)", bordercolor="#9aa1ac",
                               borderpad=2, row=1, col=2)
    fixed += 1

    per_lap = []
    for k, n in enumerate(others):
        lap = a.laps[n]
        visible = k == 0
        dt = lap.time_s - best.time_s
        name = f"Lap {n} ({_fmt_time(a.lap_times[n])})"
        traces = [
            go.Scatter(x=d, y=dt, name="Δt", line=dict(color="#222", width=1.5),
                       showlegend=False, visible=visible)
        ]
        for row, (_, col) in enumerate(CHART_ROWS[1:], start=2):
            traces.append(
                go.Scatter(x=d, y=lap.data[col], name=name, legendgroup=n, showlegend=row == 2,
                           line=dict(color=LAP_COLOR, width=1.4), visible=visible)
            )
        w = MAP_WINDOW_M
        lost = dt[w:] - dt[:-w]
        traces.append(
            go.Scatter(
                x=best.data["x_m"][:-w], y=best.data["y_m"][:-w], mode="markers",
                marker=dict(color=lost, colorscale="RdBu_r", cmid=0, size=5,
                            colorbar=dict(title=f"s lost<br>per {w} m", x=1.0, len=0.5, y=0.75)),
                text=[f"{x:.0f} m: {v:+.3f} s" for x, v in zip(d[:-w], lost)],
                hoverinfo="text", showlegend=False, visible=visible,
            )
        )
        for row, tr in zip([1] + list(range(2, n_rows + 1)), traces[:-1]):
            fig.add_trace(tr, row, 1)
        fig.add_trace(traces[-1], 1, 2)
        per_lap.append(len(traces))

    for s in a.segments:
        if s.kind == "corner":
            fig.add_vrect(x0=s.start_m, x1=s.end_m, row="all", col=1, fillcolor="#8a94a6",
                          opacity=0.10, line_width=0)
            fig.add_annotation(x=(s.start_m + s.end_m) / 2, y=1.0, yref="y domain", text=s.name,
                               showarrow=False, yanchor="bottom", font=dict(size=10), row=1, col=1)

    if others:
        buttons = []
        for k, n in enumerate(others):
            vis = [True] * fixed
            for j, count in enumerate(per_lap):
                vis += [j == k] * count
            buttons.append(dict(label=f"Lap {n} vs best", method="update", args=[{"visible": vis}]))
        fig.update_layout(updatemenus=[dict(buttons=buttons, x=0, y=1.07, xanchor="left",
                                            yanchor="bottom", showactive=True)])

    fig.update_xaxes(title_text="Distance (m)", row=n_rows, col=1)
    fig.update_yaxes(title_text=CHART_ROWS[0][0], row=1, col=1)
    fig.update_xaxes(visible=False, row=1, col=2)
    fig.update_yaxes(visible=False, scaleanchor="x2", scaleratio=1, row=1, col=2)
    fig.update_layout(
        height=1050, hovermode="x unified", margin=dict(l=50, r=20, t=90, b=40),
        legend=dict(orientation="h", x=0.25, y=1.07, xanchor="left", yanchor="bottom"),
        plot_bgcolor="#f7f8fa", paper_bgcolor="white",
    )
    return fig.to_html(full_html=False, include_plotlyjs=True, config={"displaylogo": False})


def _fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"


def _e(text: str) -> str:
    return html.escape(str(text))


_PAGE = """<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1d2128;
         background: #fff; margin: 0 auto; padding: 24px 16px 48px; max-width: 1400px; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }}
  h2 {{ font-size: 19px; margin: 36px 0 6px; }}
  .muted {{ color: #646b77; font-size: 13px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 18px; }}
  .card {{ border: 1px solid #e2e5ea; border-radius: 10px; padding: 12px 16px; min-width: 200px; }}
  .card .label {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #646b77; }}
  .card .value {{ font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .finding {{ border: 1px solid #e2e5ea; border-left: 4px solid #d6352b; border-radius: 8px;
              padding: 10px 14px; margin: 10px 0; }}
  .finding.straight {{ border-left-color: #e3a008; }}
  .finding .head {{ display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }}
  .finding .name {{ font-weight: 600; font-size: 16px; }}
  .finding .lost {{ margin-left: auto; font-weight: 600; color: #b42318;
                    font-variant-numeric: tabular-nums; }}
  .finding ul {{ margin: 6px 0 0; padding-left: 20px; }}
  .note {{ background: #fff8e6; border: 1px solid #f3dc9b; padding: 10px 14px; border-radius: 8px; }}
  .scroll {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 14px; }}
  th, td {{ padding: 5px 10px; border-bottom: 1px solid #eceef2; text-align: right; white-space: nowrap; }}
  th:first-child, td:first-child {{ text-align: left; }}
  td small {{ color: #646b77; }}
  td.best {{ background: #e7f6ec; }}
  td.bad {{ background: #fdecea; }}
  details {{ border: 1px solid #e2e5ea; border-radius: 8px; padding: 8px 14px; margin: 8px 0; }}
  summary {{ cursor: pointer; font-weight: 600; }}
  footer {{ margin-top: 40px; }}
</style></head>
<body>
{body}
</body></html>
"""
