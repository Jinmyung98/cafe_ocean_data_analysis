"""
Page 1 - Cafe operations.

POS performance at a glance: revenue/bill KPIs, where the money comes from
(category donut + top items), and when the cafe is busy (day x hour heatmap,
hourly profile, daily trend). Everything responds to the date-range and category
filters. A headline-insight row distils the three biggest takeaways.

Source: main_marts.fact_transactions x dim_items.
"""

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import data
from components import (chart_card, graph, insight_card, insight_row, kpi_card,
                        kpi_row, report_hero, section_label, style_fig)

dash.register_page(__name__, path="/", name="Operations", title="Cafe Ocean - Operations")

_TX = data.load_transactions()
_MIN_DATE = _TX["date"].min().date()
_MAX_DATE = _TX["date"].max().date()
_CATEGORIES = sorted(_TX["category"].unique())
_N_TX = len(_TX)
_N_BILLS = _TX["bill_number"].nunique()


def layout():
    return html.Div(
        [
            report_hero(
                eyebrow="Analytics Report · POS",
                title="Cafe Ocean",
                accent="Operations Dashboard",
                desc="Revenue trends · product performance · peak hours · demand profile",
                meta=[
                    ("Report Period", f"{_MIN_DATE:%b %Y} - {_MAX_DATE:%b %Y}"),
                    ("Venue", "Cafe Ocean · single location"),
                    ("Data Points", f"{_N_TX:,} line items"),
                    ("Bills", f"{_N_BILLS:,} transactions"),
                ],
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Date range", className="filter-label"),
                            dcc.DatePickerRange(
                                id="ops-dates",
                                min_date_allowed=_MIN_DATE,
                                max_date_allowed=_MAX_DATE,
                                start_date=_MIN_DATE,
                                end_date=_MAX_DATE,
                                display_format="DD MMM YYYY",
                            ),
                        ],
                        className="filter-group",
                    ),
                    html.Div(
                        [
                            html.Label("Category", className="filter-label"),
                            dcc.Dropdown(
                                id="ops-categories",
                                options=[{"label": c.title(), "value": c} for c in _CATEGORIES],
                                value=[], multi=True,
                                placeholder="All categories",
                                className="filter-dropdown",
                            ),
                        ],
                        className="filter-group grow",
                    ),
                ],
                className="filter-bar",
            ),
            kpi_row([
                kpi_card("Total revenue", "-", icon="💰",
                         delta="Net sales, all categories", id="ops-kpi-revenue"),
                kpi_card("Total bills", "-", icon="🧾",
                         delta="Unique transactions", id="ops-kpi-bills"),
                kpi_card("Avg bill value", "-", icon="🎫",
                         delta="Per transaction", id="ops-kpi-avgbill"),
                kpi_card("Active days", "-", icon="📅",
                         delta="Days with sales", id="ops-kpi-days"),
            ]),
            section_label("Revenue performance"),
            html.Div(
                [
                    chart_card("Revenue by category",
                               "Share of net sales across the menu.",
                               graph("ops-cat")),
                    chart_card("Top 15 items by revenue",
                               "The products that bring in the most money.",
                               graph("ops-items")),
                ],
                className="chart-grid two",
            ),
            chart_card(
                "Daily revenue & transactions",
                "Net sales (bars) and bill count (line) per day across the period.",
                graph("ops-trend"), wide=True,
            ),
            section_label("Demand & traffic"),
            chart_card(
                "When is the cafe busy?",
                "Distinct bills by day of week and hour - darker is busier. "
                "Use it to line staffing up against demand.",
                graph("ops-heatmap"), wide=True,
            ),
            chart_card(
                "Hourly demand profile",
                "Average bills per opening hour across the selected period.",
                graph("ops-hourly"), wide=True,
            ),
            section_label("Key takeaways"),
            html.Div(_headline_insights(), id="ops-insights"),
        ],
        className="page",
    )


# ---------------------------------------------------------------------------
# Headline insights (computed once on the full dataset)
# ---------------------------------------------------------------------------
def _headline_insights():
    rev_by_cat = _TX.groupby("category")["total"].sum().sort_values(ascending=False)
    total_rev = rev_by_cat.sum()
    top_cat = rev_by_cat.index[0]
    top_cat_share = rev_by_cat.iloc[0] / total_rev * 100

    bills = _TX.dropna(subset=["hour"]).drop_duplicates(["bill_number", "dow", "hour"])
    n_days = _TX["date"].dt.date.nunique() or 1
    peak_hour = int(bills.groupby("hour").size().idxmax())
    busy_dow = int(bills.groupby("dow").size().idxmax())
    peak_per_day = bills[bills["hour"] == peak_hour].shape[0] / n_days

    item_rev = _TX.groupby("item_name")["total"].sum().sort_values(ascending=False)
    top2 = item_rev.head(2)
    top2_share = top2.sum() / total_rev * 100

    return insight_row([
        insight_card(
            "Product mix",
            f"{top_cat.title()} leads the menu",
            f"{top_cat.title()} is the single largest revenue category over the period - "
            "the centre of gravity for pricing and promotions.",
            f"{top_cat_share:.0f}", unit="% of revenue", accent="teal"),
        insight_card(
            "Peak demand",
            f"Busiest at {peak_hour:02d}:00 on {data.DOW_NAMES[busy_dow]}s",
            "Bills cluster tightly in this window. It is the natural anchor for the "
            "roster and the best slot to push add-ons.",
            f"{peak_per_day:.0f}", unit="bills/hr", accent="accent"),
        insight_card(
            "Top sellers",
            f"{top2.index[0]} & {top2.index[1]} drive sales",
            "The two highest-grossing items alone make up a meaningful slice of revenue - "
            "protect availability and margin on these.",
            f"{top2_share:.0f}", unit="% of revenue", accent="green"),
    ])


# ---------------------------------------------------------------------------
# Filtering + figures
# ---------------------------------------------------------------------------
def _filtered(start, end, categories) -> pd.DataFrame:
    df = _TX
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]
    if categories:
        df = df[df["category"].isin(categories)]
    return df


@callback(
    Output("ops-kpi-revenue", "children"),
    Output("ops-kpi-bills", "children"),
    Output("ops-kpi-avgbill", "children"),
    Output("ops-kpi-days", "children"),
    Output("ops-cat", "figure"),
    Output("ops-items", "figure"),
    Output("ops-heatmap", "figure"),
    Output("ops-trend", "figure"),
    Output("ops-hourly", "figure"),
    Input("ops-dates", "start_date"),
    Input("ops-dates", "end_date"),
    Input("ops-categories", "value"),
)
def update(start, end, categories):
    df = _filtered(start, end, categories)

    if df.empty:
        empty = style_fig(go.Figure().add_annotation(
            text="No transactions match these filters", showarrow=False,
            font=dict(color=data.MUTED)), height=320)
        return ("-", "-", "-", "-", empty, empty, empty, empty, empty)

    revenue = df["total"].sum()
    bills = df["bill_number"].nunique()
    avg_bill = revenue / bills if bills else 0
    n_days = df["date"].dt.date.nunique()

    return (f"₹{revenue:,.0f}", f"{bills:,}", f"₹{avg_bill:,.0f}", f"{n_days:,}",
            _fig_category(df), _fig_top_items(df), _fig_heatmap(df),
            _fig_trend(df), _fig_hourly(df))


def _fig_category(df):
    g = df.groupby("category")["total"].sum().sort_values(ascending=False)
    total = g.sum()
    labels = [f"{c.title()} · {v / total * 100:.0f}%" for c, v in g.items()]
    fig = go.Figure(go.Pie(
        labels=labels, values=g.values, hole=0.62, sort=False,
        direction="clockwise", domain=dict(x=[0.0, 0.55], y=[0.0, 1.0]),
        marker=dict(colors=[data.category_color(c) for c in g.index],
                    line=dict(color="white", width=2)),
        textinfo="none",
        hovertemplate="%{label}<br>₹%{value:,.0f}<extra></extra>",
    ))
    # Centre labels, pinned to the donut domain (legend sits to the right).
    fig.add_annotation(text=f"₹{total / 1000:,.0f}K", showarrow=False,
                       xref="paper", yref="paper", x=0.275, y=0.55,
                       font=dict(family=data.SERIF_FONT, size=26, color=data.INK))
    fig.add_annotation(text="Total revenue", showarrow=False,
                       xref="paper", yref="paper", x=0.275, y=0.42,
                       font=dict(family=data.MONO_FONT, size=11, color=data.MUTED))
    fig.update_layout(
        legend=dict(orientation="v", yanchor="middle", y=0.5, x=0.62,
                    font=dict(size=12)),
        margin=dict(l=10, r=10, t=10, b=10),
    )
    return style_fig(fig, height=340)


def _fig_top_items(df):
    g = (df.groupby("item_name", as_index=False)["total"].sum()
           .nlargest(15, "total").sort_values("total"))
    fig = go.Figure(go.Bar(
        x=g["total"], y=g["item_name"], orientation="h",
        marker_color=data.ACCENT,
        text=[f"₹{v:,.0f}" for v in g["total"]],
        textposition="outside", cliponaxis=False,
        textfont=dict(family=data.MONO_FONT, size=11, color=data.MUTED),
        hovertemplate="%{y}<br>₹%{x:,.0f}<extra></extra>",
    ))
    fig.update_layout(xaxis_title=None, yaxis_title=None,
                      margin=dict(l=10, r=70, t=10, b=10))
    fig.update_xaxes(showticklabels=False, showgrid=False)
    return style_fig(fig, height=340)


def _fig_heatmap(df):
    bills = df.dropna(subset=["hour"]).drop_duplicates(["bill_number", "dow", "hour"])
    pivot = (bills.groupby(["dow", "hour"]).size()
                  .reset_index(name="bills")
                  .pivot(index="dow", columns="hour", values="bills"))
    pivot = pivot.reindex(range(7))  # Sun..Sat
    hours = sorted(bills["hour"].unique())
    pivot = pivot.reindex(columns=hours)
    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"{int(h):02d}:00" for h in pivot.columns],
        y=[data.DOW_SHORT[i] for i in pivot.index],
        colorscale="Tealgrn", colorbar=dict(title="Bills"),
        xgap=2, ygap=2,
        hovertemplate="%{y} %{x}<br>%{z} bills<extra></extra>",
    ))
    fig.update_layout(xaxis_title="Hour", yaxis_title=None)
    fig.update_yaxes(autorange="reversed")
    return style_fig(fig, height=340)


def _fig_trend(df):
    daily = df.groupby(df["date"].dt.date).agg(
        revenue=("total", "sum"),
        bills=("bill_number", "nunique"),
    ).reset_index().rename(columns={"date": "day"})
    fig = go.Figure()
    fig.add_bar(x=daily["day"], y=daily["revenue"], name="Revenue",
                marker_color=data.ACCENT, opacity=0.5,
                hovertemplate="%{x|%d %b}<br>₹%{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(
        x=daily["day"], y=daily["bills"], name="Bills", yaxis="y2",
        mode="lines", line=dict(color=data.BAD, width=2),
        hovertemplate="%{x|%d %b}<br>%{y:,} bills<extra></extra>"))
    fig.update_layout(
        xaxis_title=None,
        yaxis=dict(title="Revenue (₹)"),
        yaxis2=dict(title="Bills", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return style_fig(fig, height=320)


def _fig_hourly(df):
    bills = df.dropna(subset=["hour"]).drop_duplicates(["bill_number", "date", "hour"])
    n_days = df["date"].dt.date.nunique() or 1
    g = (bills.groupby("hour").size() / n_days).reset_index(name="avg_bills")
    fig = go.Figure(go.Bar(
        x=[f"{int(h):02d}:00" for h in g["hour"]], y=g["avg_bills"],
        marker_color=data.GREEN,
        hovertemplate="%{x}<br>%{y:.1f} bills/day<extra></extra>",
    ))
    fig.update_layout(xaxis_title="Hour", yaxis_title="Avg bills / day")
    return style_fig(fig, height=300)
