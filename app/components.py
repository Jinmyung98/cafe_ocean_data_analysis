"""
Shared UI building blocks for the report-style dashboard: the dark hero header,
mono section labels, KPI tiles, titled chart cards, and insight/recommendation
cards. Keeping them here gives every page the same magazine look without
repeating layout code.
"""

from __future__ import annotations

from dash import dcc, html

from data import GRID, INK, MUTED

# Plotly needs concrete font names (no CSS vars); mirror the stylesheet stacks.
SANS = "Inter, 'Segoe UI', system-ui, sans-serif"
SERIF = "Fraunces, Georgia, 'Times New Roman', serif"
MONO = "IBM Plex Mono, Consolas, monospace"


def style_fig(fig, *, height: int | None = None, legend_top: bool = False):
    """Apply the shared template to a Plotly figure (in place) and return it."""
    fig.update_layout(
        template="plotly_white",
        font=dict(family=SANS, color=INK, size=13),
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="white",
        plot_bgcolor="white",
        hoverlabel=dict(font_family=SANS),
    )
    if height:
        fig.update_layout(height=height)
    if legend_top:
        fig.update_layout(
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="left", x=0, title_text=""),
        )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return fig


# ---------------------------------------------------------------------------
# Report hero header
# ---------------------------------------------------------------------------
def report_hero(eyebrow: str, title: str, accent: str, desc: str,
                meta: list[tuple[str, str]], badge: str = "Prepared by Cafe Ocean Analytics"):
    """Dark hero band: mono eyebrow, two-line serif title, descriptor, and a
    right-hand metadata block + 'prepared by' badge - like the reference report."""
    meta_rows = [
        html.Div([html.Span(f"{label}: ", className="meta-label"), html.Span(value)],
                 className="meta-row")
        for label, value in meta
    ]
    return html.Header(
        [
            html.Div(
                [
                    html.Div(eyebrow, className="hero-eyebrow"),
                    html.H1([title, html.Span(accent, className="accent")],
                            className="hero-title"),
                    html.P(desc, className="hero-desc"),
                ],
                className="hero-left",
            ),
            html.Div(
                [
                    html.Div(meta_rows, className="hero-meta"),
                    html.Div([html.Span("☕ "), badge], className="hero-badge"),
                ],
                className="hero-right",
            ),
        ],
        className="report-hero",
    )


def section_label(text: str):
    """A mono uppercase label with a trailing rule, e.g. 'REVENUE PERFORMANCE ----'."""
    return html.Div(html.Span(text), className="section-label")


# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------
def kpi_card(label: str, value: str, *, icon: str = "", delta: str | None = None,
             delta_kind: str = "neutral", id: str | None = None):
    """A KPI tile: icon badge, mono label, big serif value, optional context line.

    If `id` is given the value span gets that id so a callback can update it.
    `delta` is a short context string (mono); `delta_kind` colours it
    ('up' green, 'down' red, else muted).
    """
    value_kwargs = {"className": "kpi-value"}
    if id:
        value_kwargs["id"] = id
    children = []
    if icon:
        children.append(html.Div(icon, className="kpi-icon"))
    children += [
        html.Div(label, className="kpi-label"),
        html.Div(value, **value_kwargs),
    ]
    if delta is not None:
        children.append(html.Div(delta, className=f"kpi-delta {delta_kind}"))
    return html.Div(children, className="kpi-card")


def kpi_row(cards):
    return html.Div(cards, className="kpi-row")


# ---------------------------------------------------------------------------
# Chart cards
# ---------------------------------------------------------------------------
def chart_card(title: str, caption: str, graph, *, wide: bool = False):
    """Wrap a dcc.Graph (or any content) with a serif title and a caption."""
    return html.Div(
        [
            html.Div(title, className="card-title"),
            html.Div(caption, className="card-caption"),
            graph,
        ],
        className="chart-card" + (" wide" if wide else ""),
    )


def graph(fig_id: str, **kwargs):
    """A configured dcc.Graph with the mode bar trimmed down."""
    config = {"displaylogo": False,
              "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]}
    return dcc.Graph(id=fig_id, config=config, **kwargs)


# ---------------------------------------------------------------------------
# Insight / recommendation cards
# ---------------------------------------------------------------------------
def insight_card(eyebrow: str, title: str, body, metric: str, *,
                 unit: str = "", accent: str = "accent"):
    """A recommendation tile: mono eyebrow, serif headline, body text, and a big
    serif metric callout. `accent` selects the left-border + metric colour
    ('accent' | 'teal' | 'green' | 'amber')."""
    metric_children = [metric]
    if unit:
        metric_children.append(html.Span(unit, className="unit"))
    return html.Div(
        [
            html.Div(eyebrow, className="insight-eyebrow"),
            html.Div(title, className="insight-title"),
            html.Div(body, className="insight-body"),
            html.Div(metric_children, className="insight-metric"),
        ],
        className=f"insight-card {accent}",
    )


def insight_row(cards):
    return html.Div(cards, className="insight-row")


# ---------------------------------------------------------------------------
# Missing-output placeholder
# ---------------------------------------------------------------------------
def missing_output(message: str):
    """Friendly placeholder shown when an optimiser output CSV isn't there yet."""
    return html.Div(
        [
            html.Div("⚠", className="missing-icon"),  # warning sign
            html.Div("Nothing to show yet", className="missing-title"),
            html.P(message, className="missing-body"),
        ],
        className="missing-output",
    )
