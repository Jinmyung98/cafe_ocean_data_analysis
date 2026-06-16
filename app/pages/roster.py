"""
Page 2 - Roster.

Turns outputs/schedule.csv into a readable fortnight plan: a Gantt of shift blocks
(one row per staff, coloured by role, with leave marked), hours against each
person's guaranteed floor and overtime cap, and a coverage-vs-demand check for a
chosen day. KPIs summarise hours, wage cost, and leave.

Sources: outputs/schedule.csv + dim_staff + bridge_staff_leave + ref_demand_by_slot.
Needs src/staff_optimiser.py to have been run.
"""

import datetime

import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import data
from components import (chart_card, graph, insight_card, insight_row, kpi_card,
                        kpi_row, missing_output, report_hero, section_label,
                        style_fig)

dash.register_page(__name__, path="/roster", name="Roster", title="Cafe Ocean - Roster")

_RUN_HINT = ("The roster comes from outputs/schedule.csv, which isn't there yet. "
             "Run  python src/staff_optimiser.py  to generate it, then reload.")


def _hero(meta):
    return report_hero(
        eyebrow="Analytics Report · Staffing",
        title="Cafe Ocean",
        accent="Fortnight Roster",
        desc="Shift plan · hours vs guarantee · demand coverage",
        meta=meta,
    )


def layout():
    sched = data.load_schedule()
    if sched is None:
        return html.Div(
            [_hero([("Source", "outputs/schedule.csv"), ("Status", "not generated yet")]),
             missing_output(_RUN_HINT)],
            className="page")

    staff = data.load_staff()
    dates = sorted(sched["date"].unique())
    staff_options = [{"label": f"{r.staff_id} - {r.staff_name}", "value": r.staff_id}
                     for r in staff.itertuples()]
    date_options = [{"label": f"{d:%a %d %b}", "value": d.isoformat()} for d in dates]

    return html.Div(
        [
            _hero([
                ("Planning Period", f"{dates[0]:%d %b} - {dates[-1]:%d %b %Y}"),
                ("Horizon", f"{len(dates)} days · {data.N_SLOTS} slots/day"),
                ("Staff", f"{len(staff)} rostered"),
                ("Slot Length", "30 minutes"),
            ]),
            kpi_row([
                kpi_card("Scheduled hours", "-", icon="🕒",
                         delta="Over the fortnight", id="ros-kpi-hours"),
                kpi_card("Wage cost", "-", icon="💰",
                         delta="Minimum-cost roster", id="ros-kpi-cost"),
                kpi_card("Leave days", "-", icon="🌴",
                         delta="Approved & honoured", id="ros-kpi-leave"),
                kpi_card("Staff rostered", "-", icon="👥",
                         delta="With at least one shift", id="ros-kpi-staff"),
            ]),
            section_label("Shift plan"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Staff", className="filter-label"),
                            dcc.Dropdown(id="ros-staff", options=staff_options,
                                         value=[], multi=True,
                                         placeholder="All staff",
                                         className="filter-dropdown"),
                        ],
                        className="filter-group grow",
                    ),
                ],
                className="filter-bar",
            ),
            chart_card(
                "Fortnight shift plan",
                "One row per staff member; each bar is a continuous shift block, "
                "coloured by role. Shaded blocks are approved leave.",
                graph("ros-gantt"), wide=True,
            ),
            section_label("Hours & coverage"),
            chart_card(
                "Hours vs guaranteed floor and cap",
                "Scheduled hours per person against their guaranteed minimum and the "
                f"guaranteed + {data.MAX_EXTRA_HOURS}h overtime cap.",
                graph("ros-hours"), wide=True,
            ),
            html.Div(
                [
                    html.Label("Coverage day", className="filter-label"),
                    dcc.Dropdown(id="ros-day", options=date_options,
                                 value=dates[0].isoformat(), clearable=False,
                                 className="filter-dropdown"),
                ],
                className="filter-bar",
            ),
            chart_card(
                "Coverage vs demand",
                "Scheduled headcount per 30-minute slot against the minimum staffing "
                "the demand model asks for, on the selected day.",
                graph("ros-coverage"), wide=True,
            ),
            section_label("Roster takeaways"),
            _roster_insights(sched, staff, data.load_leave()),
        ],
        className="page",
    )


def _roster_insights(sched, staff, leave):
    hours_per = sched.groupby("staff_id").size() * data.TAU
    total_hours = hours_per.sum()
    rate = dict(zip(staff["staff_id"], staff["hourly_rate"]))
    wage_cost = sum(h * rate.get(sid, 0) for sid, h in hours_per.items())
    capped_capacity = float((staff["guaranteed_hours"] + data.MAX_EXTRA_HOURS).sum())
    util = total_hours / capped_capacity * 100 if capped_capacity else 0
    dates = set(sched["date"].unique())
    leave_days = leave[leave["leave_date"].isin(dates)].shape[0]

    return insight_row([
        insight_card(
            "Cost",
            "Minimum-cost roster",
            "Lowest wage bill that still covers every modelled demand slot while "
            "honouring break rules, the 8-hour span cap and single daily shifts.",
            f"₹{wage_cost:,.0f}", accent="teal"),
        insight_card(
            "Capacity headroom",
            f"Running at {util:.0f}% of capped capacity",
            f"Total hours sit close to the guaranteed + {data.MAX_EXTRA_HOURS}h ceiling - "
            "little slack if demand rises or someone calls in sick. A fragility flag.",
            f"{util:.0f}", unit="% used", accent="amber"),
        insight_card(
            "Leave",
            f"{leave_days} leave-days absorbed",
            "Approved leave is fully honoured; each person's guaranteed hours are "
            "pro-rated for the working days they lose.",
            f"{leave_days}", unit="days", accent="green"),
    ])


# ---------------------------------------------------------------------------
# Shift-block assembly
# ---------------------------------------------------------------------------
def _shift_rows(sched, staff, leave):
    """Long-form rows for the Gantt: one per shift block, plus one per leave day."""
    name = dict(zip(staff["staff_id"], staff["staff_name"]))
    role = dict(zip(staff["staff_id"], staff["role"]))
    label = {sid: f"{name[sid]}" for sid in staff["staff_id"]}

    leave_set = {(r.staff_id, r.leave_date): r.leave_type for r in leave.itertuples()}
    dates = sorted(sched["date"].unique())

    rows = []
    for (sid, date), grp in sched.groupby(["staff_id", "date"]):
        for start, end in data.to_blocks(grp["slot_pos"]):
            x0, x1 = data.block_datetimes(date, start, end)
            rows.append(dict(Staff=label.get(sid, sid), Role=role.get(sid, "?"),
                             Start=x0, Finish=x1,
                             Day=f"{date:%a %d %b}", Detail=f"{start} - {end}"))
    # Leave bars span the full operating window so they read clearly.
    for (sid, date), ltype in leave_set.items():
        if sid not in label or date not in dates:
            continue
        x0, x1 = data.block_datetimes(date, "10:00", "01:00")
        rows.append(dict(Staff=label[sid], Role="LEAVE", Start=x0, Finish=x1,
                         Day=f"{date:%a %d %b}", Detail=f"{ltype} leave"))
    return pd.DataFrame(rows)


def _staff_order(staff, selected):
    s = staff if not selected else staff[staff["staff_id"].isin(selected)]
    s = s.sort_values(["role", "staff_name"])
    return list(s["staff_name"])


@callback(
    Output("ros-kpi-hours", "children"),
    Output("ros-kpi-cost", "children"),
    Output("ros-kpi-leave", "children"),
    Output("ros-kpi-staff", "children"),
    Output("ros-gantt", "figure"),
    Output("ros-hours", "figure"),
    Input("ros-staff", "value"),
)
def update_main(selected):
    sched = data.load_schedule()
    staff = data.load_staff()
    leave = data.load_leave()

    view = sched if not selected else sched[sched["staff_id"].isin(selected)]
    staff_view = staff if not selected else staff[staff["staff_id"].isin(selected)]

    # KPIs (reflect the staff filter)
    hours_per = view.groupby("staff_id").size() * data.TAU
    total_hours = hours_per.sum()
    rate = dict(zip(staff["staff_id"], staff["hourly_rate"]))
    wage_cost = sum(h * rate.get(sid, 0) for sid, h in hours_per.items())
    dates = set(sched["date"].unique())
    ids = set(staff_view["staff_id"])
    leave_days = leave[(leave["staff_id"].isin(ids)) & (leave["leave_date"].isin(dates))].shape[0]

    kpis = (f"{total_hours:,.0f} h", f"₹{wage_cost:,.0f}",
            f"{leave_days}", f"{view['staff_id'].nunique()}")

    return (*kpis, _fig_gantt(view, staff_view, leave), _fig_hours(view, staff_view))


def _fig_gantt(view, staff_view, leave):
    rows = _shift_rows(view, staff_view, leave)
    order = _staff_order(staff_view, None)
    if rows.empty:
        return style_fig(go.Figure().add_annotation(
            text="No shifts for this selection", showarrow=False,
            font=dict(color=data.MUTED)), height=420)

    role_seq = [r for r in data.ROLE_COLORS if r in set(rows["Role"])]
    fig = px.timeline(
        rows, x_start="Start", x_end="Finish", y="Staff", color="Role",
        color_discrete_map=data.ROLE_COLORS,
        category_orders={"Staff": order, "Role": role_seq},
        custom_data=["Day", "Detail"],
    )
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>%{customdata[0]}<br>"
                      "%{customdata[1]}<extra></extra>",
        marker_line_width=0,
    )
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title=None, dtick=86400000.0, tickformat="%a\n%d %b",
                     ticklabelmode="period", showgrid=True)
    height = max(360, 60 + 26 * len(order))
    return style_fig(fig, height=height, legend_top=True)


def _fig_hours(view, staff_view):
    hours_per = (view.groupby("staff_id").size() * data.TAU).to_dict()
    s = staff_view.sort_values(["role", "staff_name"]).copy()
    s["hours"] = s["staff_id"].map(hours_per).fillna(0)
    s["floor"] = s["guaranteed_hours"]
    s["cap"] = s["guaranteed_hours"] + data.MAX_EXTRA_HOURS

    def _bar_color(r):
        if r.hours < r.floor - 0.01:
            return data.BAD
        if r.hours > r.cap + 0.01:
            return data.WARN
        return data.GOOD

    s["color"] = [_bar_color(r) for r in s.itertuples()]

    fig = go.Figure()
    fig.add_bar(
        x=s["hours"], y=s["staff_name"], orientation="h",
        marker_color=s["color"], name="Scheduled hours",
        hovertemplate="%{y}<br>%{x:.1f} h<extra></extra>",
    )
    fig.add_trace(go.Scatter(
        x=s["floor"], y=s["staff_name"], mode="markers", name="Guaranteed floor",
        marker=dict(symbol="line-ns", color=data.INK, size=16,
                    line=dict(width=2, color=data.INK)),
        hovertemplate="%{y}<br>floor %{x:.0f} h<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=s["cap"], y=s["staff_name"], mode="markers", name="Overtime cap",
        marker=dict(symbol="line-ns", color=data.MUTED, size=16,
                    line=dict(width=2, color=data.MUTED)),
        hovertemplate="%{y}<br>cap %{x:.0f} h<extra></extra>"))
    fig.update_layout(xaxis_title="Hours over the fortnight", yaxis_title=None)
    fig.update_yaxes(autorange="reversed")
    height = max(320, 60 + 30 * len(s))
    return style_fig(fig, height=height, legend_top=True)


@callback(
    Output("ros-coverage", "figure"),
    Input("ros-day", "value"),
)
def update_coverage(day_iso):
    sched = data.load_schedule()
    demand = data.load_demand_by_slot()
    date = datetime.date.fromisoformat(day_iso)
    dow = (date.weekday() + 1) % 7  # DuckDB DAYOFWEEK convention

    day_sched = sched[sched["date"] == date]
    scheduled = day_sched.groupby("slot")["staff_id"].nunique().to_dict()
    dmd = demand[demand["day_of_week"] == dow].set_index("slot")["min_staff"].to_dict()

    labels = [s[:5] for s in data.SLOT_LABELS]
    sched_y = [scheduled.get(lbl, 0) for lbl in labels]
    dmd_y = [dmd.get(lbl, 0) for lbl in labels]

    fig = go.Figure()
    fig.add_bar(x=labels, y=sched_y, name="Scheduled headcount",
                marker_color=data.ACCENT,
                hovertemplate="%{x}<br>%{y} scheduled<extra></extra>")
    fig.add_trace(go.Scatter(
        x=labels, y=dmd_y, name="Minimum required", mode="lines",
        line=dict(color=data.BAD, width=2, shape="hv"),
        hovertemplate="%{x}<br>need %{y}<extra></extra>"))
    fig.update_layout(xaxis_title="Slot", yaxis_title="Staff",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_yaxes(dtick=1)
    return style_fig(fig, height=340)
