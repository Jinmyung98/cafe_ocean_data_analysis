"""
Page 3 - Stock.

Reads outputs/purchase_order.csv (the weekly order the stock optimiser produced)
and shows what it costs, who supplies it, how full storage gets after delivery, and
the demand behind each line. A separate table lists ingredients the optimiser had
to drop for shelf-life or capacity reasons.

Sources: outputs/purchase_order.csv + ref_ingredient_demand_weekly
         + ref_stockroom_capacity + dim_suppliers (+ BOM for ingredient categories).
Needs src/stock_optimiser.py to have been run.
"""

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import dash_table, html

import data
from components import (chart_card, graph, insight_card, insight_row, kpi_card,
                        kpi_row, missing_output, report_hero, section_label,
                        style_fig)

dash.register_page(__name__, path="/stock", name="Stock", title="Cafe Ocean - Stock")

_RUN_HINT = ("The purchase order comes from outputs/purchase_order.csv, which isn't "
             "there yet. Run  python src/stock_optimiser.py  to generate it, then reload.")


def _hero(meta):
    return report_hero(
        eyebrow="Analytics Report · Purchasing",
        title="Cafe Ocean",
        accent="Weekly Stock Order",
        desc="Order cost · supplier mix · storage headroom · exclusions",
        meta=meta,
    )


def _enriched_order():
    """Purchase order joined to supplier names, ingredient category, and capacity."""
    po = data.load_purchase_order()
    if po is None:
        return None
    suppliers = data.load_suppliers()[["supplier_id", "supplier_name"]]
    cats = data.load_ingredient_category()
    caps = data.load_capacity()[["ingredient_id", "max_quantity", "location"]]

    po = (po.merge(suppliers, on="supplier_id", how="left")
            .merge(cats, on="ingredient_id", how="left")
            .merge(caps, on="ingredient_id", how="left"))
    po["supplier_name"] = po["supplier_name"].fillna(po["supplier_id"])
    po["category"] = po["category"].fillna("MISC")
    return po


def layout():
    po = _enriched_order()
    if po is None:
        return html.Div(
            [_hero([("Source", "outputs/purchase_order.csv"), ("Status", "not generated yet")]),
             missing_output(_RUN_HINT)], className="page")

    total_cost = po["purchase_cost"].sum()
    n_lines = int((po["purchase_units"] > 0).sum())
    n_suppliers = po.loc[po["purchase_units"] > 0, "supplier_id"].nunique()
    excl = data.excluded_ingredients()
    n_total = len(po) + len(excl)

    return html.Div(
        [
            _hero([
                ("Order Cycle", "Weekly"),
                ("Ingredients", f"{n_total} tracked"),
                ("Suppliers", f"{n_suppliers} ordered"),
                ("Safety Stock", f"{data.SAFETY_STOCK:.0%} above p75"),
            ]),
            kpi_row([
                kpi_card("Total order cost", f"₹{total_cost:,.0f}", icon="💰",
                         delta="This week's spend"),
                kpi_card("Ingredient lines", f"{n_lines}", icon="📦",
                         delta="With units ordered"),
                kpi_card("Suppliers", f"{n_suppliers}", icon="🚚",
                         delta="To place orders with"),
                kpi_card("Excluded", f"{len(excl)}", icon="⚠️",
                         delta="Shelf-life / capacity",
                         delta_kind="down" if len(excl) else "neutral"),
            ]),
            section_label("Order breakdown"),
            html.Div(
                [
                    chart_card("Order cost by supplier",
                               "Where the spend goes - useful for consolidating orders.",
                               graph("stk-supplier", figure=_fig_supplier(po))),
                    chart_card("Order cost by menu category",
                               "Spend grouped by the menu category each ingredient feeds.",
                               graph("stk-category", figure=_fig_category(po))),
                ],
                className="chart-grid two",
            ),
            section_label("Storage & demand"),
            chart_card(
                "Storage utilisation after delivery",
                "How full each ingredient's storage is once the order lands "
                "(stock after delivery / capacity). Bars past 100% would overflow.",
                graph("stk-capacity", figure=_fig_capacity(po)), wide=True,
            ),
            chart_card(
                "Weekly demand per ingredient (p75)",
                "The 75th-percentile weekly consumption the order is sized to cover.",
                graph("stk-demand", figure=_fig_demand(po)), wide=True,
            ),
            section_label("Exclusions"),
            html.Div(
                [
                    html.Div("Excluded from this order", className="card-title"),
                    html.Div(
                        "Ingredients the optimiser drops before solving: shelf life or "
                        "storage can't hold a full week's cover at these volumes. "
                        "Order them more often or expand storage.",
                        className="card-caption"),
                    _excluded_table(excl),
                ],
                className="chart-card wide",
            ),
            section_label("Stock takeaways"),
            _stock_insights(po, excl),
        ],
        className="page",
    )


def _stock_insights(po, excl):
    total_cost = po["purchase_cost"].sum()
    by_supplier = po.groupby("supplier_name")["purchase_cost"].sum().sort_values(ascending=False)
    top_supplier = by_supplier.index[0]
    top_share = by_supplier.iloc[0] / total_cost * 100 if total_cost else 0

    return insight_row([
        insight_card(
            "Spend",
            "Minimum-cost weekly order",
            "Cheapest order that covers a week of p75 demand plus a "
            f"{data.SAFETY_STOCK:.0%} buffer, within every shelf-life and storage limit.",
            f"₹{total_cost:,.0f}", accent="teal"),
        insight_card(
            "Supplier concentration",
            f"{top_supplier} dominates spend",
            "The bulk of the bill sits with one supplier - worth negotiating terms there, "
            "but a single point of failure for deliveries.",
            f"{top_share:.0f}", unit="% of cost", accent="amber"),
        insight_card(
            "Perishables",
            f"{len(excl)} items can't be bulk-ordered",
            "Shelf life or storage can't hold a full week's cover for these - they belong "
            "on a shorter delivery cycle rather than this weekly order.",
            f"{len(excl)}", unit="excluded", accent="green"),
    ])


def _fig_supplier(po):
    g = (po.groupby("supplier_name", as_index=False)["purchase_cost"].sum()
           .sort_values("purchase_cost"))
    fig = go.Figure(go.Bar(
        x=g["purchase_cost"], y=g["supplier_name"], orientation="h",
        marker_color=data.ACCENT,
        hovertemplate="%{y}<br>₹%{x:,.0f}<extra></extra>"))
    fig.update_layout(xaxis_title="Order cost (₹)", yaxis_title=None)
    return style_fig(fig, height=320)


def _fig_category(po):
    g = (po.groupby("category", as_index=False)["purchase_cost"].sum()
           .sort_values("purchase_cost"))
    fig = go.Figure(go.Bar(
        x=g["purchase_cost"], y=g["category"].str.title(), orientation="h",
        marker_color=[data.category_color(c) for c in g["category"]],
        hovertemplate="%{y}<br>₹%{x:,.0f}<extra></extra>"))
    fig.update_layout(xaxis_title="Order cost (₹)", yaxis_title=None)
    return style_fig(fig, height=320)


def _fig_capacity(po):
    df = po.dropna(subset=["max_quantity"]).copy()
    df = df[df["max_quantity"] > 0]
    df["util"] = df["stock_after_delivery"] / df["max_quantity"] * 100
    df = df.sort_values("util")

    def _col(u):
        if u > 100:
            return data.BAD
        if u >= 85:
            return data.WARN
        return data.GOOD

    fig = go.Figure(go.Bar(
        x=df["util"], y=df["ingredient_name"], orientation="h",
        marker_color=[_col(u) for u in df["util"]],
        customdata=df[["stock_after_delivery", "max_quantity", "unit"]],
        hovertemplate="%{y}<br>%{x:.0f}%% full<br>"
                      "%{customdata[0]:,.0f} / %{customdata[1]:,.0f} %{customdata[2]}<extra></extra>"))
    fig.add_vline(x=100, line_dash="dash", line_color=data.MUTED)
    fig.update_layout(xaxis_title="Storage utilisation (%)", yaxis_title=None)
    height = max(340, 60 + 24 * len(df))
    return style_fig(fig, height=height)


def _fig_demand(po):
    g = po.sort_values("p75_weekly_demand")
    fig = go.Figure(go.Bar(
        x=g["p75_weekly_demand"], y=g["ingredient_name"], orientation="h",
        marker_color=data.GOOD, customdata=g[["unit"]],
        hovertemplate="%{y}<br>%{x:,.0f} %{customdata[0]}/week<extra></extra>"))
    fig.update_layout(xaxis_title="p75 weekly demand", yaxis_title=None)
    height = max(340, 60 + 24 * len(g))
    return style_fig(fig, height=height)


def _excluded_table(excl: pd.DataFrame):
    if excl.empty:
        return html.P("None - every ingredient fits within shelf life and storage.",
                      className="empty-note")
    show = excl.rename(columns={
        "ingredient_id": "ID", "ingredient_name": "Ingredient",
        "unit": "Unit", "flag": "Reason", "detail": "Detail"})
    return dash_table.DataTable(
        data=show.to_dict("records"),
        columns=[{"name": c, "id": c} for c in show.columns],
        style_as_list_view=True,
        style_header={"backgroundColor": "#f2f6fa", "fontWeight": "600",
                      "color": data.INK, "border": "none"},
        style_cell={"fontFamily": "Inter, 'Segoe UI', sans-serif", "fontSize": "13px",
                    "padding": "10px 12px", "textAlign": "left", "border": "none",
                    "color": data.INK},
        style_data={"borderBottom": f"1px solid {data.GRID}"},
        style_data_conditional=[
            {"if": {"filter_query": "{Reason} = 'Shelf life'", "column_id": "Reason"},
             "color": data.WARN, "fontWeight": "600"},
            {"if": {"filter_query": "{Reason} = 'Capacity'", "column_id": "Reason"},
             "color": data.BAD, "fontWeight": "600"},
        ],
    )
