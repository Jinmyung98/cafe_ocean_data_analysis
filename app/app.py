"""
Cafe Ocean analytics dashboard (Plotly Dash, multi-page).

Three pages built on the project warehouse and optimiser outputs:
  /        Operations  -- POS revenue, demand, item mix
  /roster  Roster      -- fortnight shift plan, hours, coverage
  /stock   Stock       -- weekly purchase order, capacity, exclusions

Run:
    python app/app.py
then open http://127.0.0.1:8050

Reads data/cafe_ocean.duckdb read-only and the CSVs in outputs/. The roster and
stock pages need the optimisers to have been run first (src/staff_optimiser.py and
src/stock_optimiser.py); until then they show a friendly prompt instead of crashing.
"""

from dash import Dash, dcc, html, page_container

app = Dash(
    __name__,
    use_pages=True,
    title="Cafe Ocean Analytics",
    suppress_callback_exceptions=True,
)
server = app.server  # for WSGI / deployment

NAV_LINKS = [("Operations", "/"), ("Roster", "/roster"), ("Stock", "/stock")]


def navbar():
    return html.Nav(
        [
            html.Div(
                [html.Span("🌊", className="brand-mark"),
                 html.Span("Cafe Ocean", className="brand-name")],
                className="brand",
            ),
            html.Div(
                [dcc.Link(label, href=href, className="nav-link")
                 for label, href in NAV_LINKS],
                className="nav-links",
            ),
        ],
        className="navbar",
    )


def footer():
    return html.Footer(
        [
            html.Div(
                "Data shown is illustrative - derived from the public Kaggle Cafe "
                "Ocean transaction dataset with manually-maintained demo seeds (wages, "
                "ingredient prices, bill of materials, storage capacities, leave). "
                "Figures are not a real client's financials.",
                className="footer-disclaimer",
            ),
            html.Div(
                [
                    html.Div("Cafe Ocean Analytics", className="footer-brand"),
                    html.Div("Operations · Staffing · Stock", className="footer-tag"),
                ],
                className="footer-right",
            ),
        ],
        className="footer-band",
    )


app.layout = html.Div(
    [
        navbar(),
        html.Main(page_container, className="page-body"),
        footer(),
    ],
    className="app-shell",
)

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=8050)
