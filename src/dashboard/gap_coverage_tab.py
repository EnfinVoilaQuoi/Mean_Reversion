"""
Module pour l'onglet Gap Coverage du dashboard.

Fournit des visualisations et statistiques sur la couverture des gaps de scraping.
"""

import logging
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from dash import dash_table, dcc, html
from plotly.subplots import make_subplots

from ..database.models import GapAttempt, ScrapedGap, Token
from ..analysis.gap_tracking import get_scraping_summary

logger = logging.getLogger(__name__)


def fetch_gap_coverage_data(token_id: int, days_back: int = 7) -> dict:
    """
    Récupère les données de couverture des gaps pour un token.

    Args:
        token_id: ID du token
        days_back: Nombre de jours en arrière pour l'analyse

    Returns:
        dict avec:
        - gaps_summary: DataFrame des gaps avec statut
        - scraping_stats: Statistiques de scraping par méthode
        - nitter_stats: Statistiques par instance Nitter
        - summary_stats: Résumé global des gaps
    """
    try:
        token = Token.get_by_id(token_id)

        # 1. Récupérer le résumé de scraping
        summary = get_scraping_summary(token, days=days_back)

        # 2. Récupérer tous les gaps du token
        gaps = list(ScrapedGap.select().where(ScrapedGap.token == token))

        # 3. Créer DataFrame des gaps
        gaps_data = []
        for gap in gaps:
            gaps_data.append({
                'Gap ID': gap.id,
                'Depuis': gap.since_date,
                'Jusqu\'à': gap.until_date,
                'Heures Attendues': gap.expected_hours,
                'Heures Couvertes': gap.covered_hours,
                'Complétude (%)': f"{gap.completeness_pct:.1f}",
                'Statut': gap.status,
                'Méthodes Essayées': gap.methods_tried or '-',
                'Première Tentative': gap.first_attempt.strftime('%Y-%m-%d %H:%M') if gap.first_attempt else '-',
                'Dernière Tentative': gap.last_attempt.strftime('%Y-%m-%d %H:%M') if gap.last_attempt else '-',
            })

        gaps_df = pd.DataFrame(gaps_data)

        # 4. Statistiques de scraping par méthode
        scraping_stats = []
        for method, stats in summary['methods'].items():
            scraping_stats.append({
                'Méthode': method,
                'Tentatives': stats['total_attempts'],
                'Succès': stats['success'],
                'Échecs': stats['failed'],
                'Taux de Succès (%)': f"{stats['success_rate']:.1f}",
                'Tweets Récupérés': stats['total_tweets'],
            })

        scraping_stats_df = pd.DataFrame(scraping_stats)

        # 5. Statistiques par instance Nitter
        nitter_stats = []
        for instance, stats in summary['nitter_instances'].items():
            nitter_stats.append({
                'Instance': instance,
                'Tentatives': stats['attempts'],
                'Succès': stats['success'],
                'Taux de Succès (%)': f"{stats['success_rate']:.1f}",
            })

        nitter_stats_df = pd.DataFrame(nitter_stats)

        return {
            'gaps_summary': gaps_df,
            'scraping_stats': scraping_stats_df,
            'nitter_stats': nitter_stats_df,
            'summary_stats': summary['gaps'],
            'total_attempts': summary['total_attempts'],
            'token_cashtag': token.cashtag,
        }

    except Exception as e:
        logger.error(f"Erreur fetch_gap_coverage_data: {e}", exc_info=True)
        return {
            'gaps_summary': pd.DataFrame(),
            'scraping_stats': pd.DataFrame(),
            'nitter_stats': pd.DataFrame(),
            'summary_stats': {'total': 0, 'complete': 0, 'partial': 0, 'exhausted': 0, 'pending': 0, 'avg_completeness': 0},
            'total_attempts': 0,
            'token_cashtag': 'N/A',
        }


def create_gap_coverage_tab():
    """Crée le contenu de l'onglet Gap Coverage"""
    return html.Div([
        # Header de l'onglet
        html.Div([
            html.H2(
                "🔍 Gap Coverage - Analyse de Complétude",
                style={"color": "#34495e", "marginBottom": "15px"},
            ),
            html.P(
                "Suivi détaillé de la couverture des gaps de scraping avec analyse par méthode et instance Nitter",
                style={"color": "#7f8c8d", "fontSize": "14px"},
            ),
        ]),

        html.Hr(),

        # Sélecteur de token
        html.Div([
            html.H3("Sélectionner un Token", style={"color": "#34495e", "marginBottom": "10px"}),
            dcc.Dropdown(
                id="gap-token-selector",
                options=[],
                value=None,
                placeholder="Sélectionner un Token pour l'analyse des gaps",
            ),
        ], style={"marginBottom": "30px"}),

        # Panneau de résumé global
        html.Div(id="gap-summary-panel", style={"marginBottom": "30px"}),

        # Section: Statistiques de scraping par méthode
        html.Div([
            html.H3(
                "📊 Performance par Méthode de Scraping",
                style={"color": "#34495e", "marginBottom": "15px"},
            ),
            dcc.Loading(
                id="loading-scraping-stats",
                type="default",
                children=[
                    dcc.Graph(id="scraping-methods-chart"),
                    dash_table.DataTable(  # type: ignore[attr-defined]
                        id="scraping-stats-table",
                        columns=[],
                        data=[],
                        style_table={"overflowX": "auto"},
                        style_header={
                            "backgroundColor": "#3498db",
                            "color": "white",
                            "fontWeight": "bold",
                            "textAlign": "left",
                        },
                        style_cell={
                            "textAlign": "left",
                            "padding": "10px",
                            "fontSize": "13px",
                        },
                    ),
                ],
            ),
        ], style={
            "padding": "20px",
            "border": "1px solid #ecf0f1",
            "borderRadius": "10px",
            "backgroundColor": "white",
            "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            "marginBottom": "30px",
        }),

        # Section: Statistiques par instance Nitter
        html.Div([
            html.H3(
                "🌐 Performance des Instances Nitter",
                style={"color": "#34495e", "marginBottom": "15px"},
            ),
            dcc.Loading(
                id="loading-nitter-stats",
                type="default",
                children=[
                    dcc.Graph(id="nitter-instances-chart"),
                    dash_table.DataTable(  # type: ignore[attr-defined]
                        id="nitter-stats-table",
                        columns=[],
                        data=[],
                        style_table={"overflowX": "auto"},
                        style_header={
                            "backgroundColor": "#9b59b6",
                            "color": "white",
                            "fontWeight": "bold",
                            "textAlign": "left",
                        },
                        style_cell={
                            "textAlign": "left",
                            "padding": "10px",
                            "fontSize": "13px",
                        },
                    ),
                ],
            ),
        ], style={
            "padding": "20px",
            "border": "1px solid #ecf0f1",
            "borderRadius": "10px",
            "backgroundColor": "white",
            "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            "marginBottom": "30px",
        }),

        # Section: Tableau détaillé des gaps
        html.Div([
            html.H3(
                "📋 Liste Complète des Gaps",
                style={"color": "#34495e", "marginBottom": "15px"},
            ),
            html.P(
                "Filtrer par statut pour voir les gaps nécessitant une attention",
                style={"color": "#7f8c8d", "fontSize": "14px", "marginBottom": "15px"},
            ),
            dcc.RadioItems(
                id="gap-status-filter",
                options=[
                    {"label": " Tous", "value": "all"},
                    {"label": " Partiels", "value": "partial"},
                    {"label": " Complets", "value": "complete"},
                    {"label": " Épuisés", "value": "all_methods_exhausted"},
                    {"label": " En attente", "value": "pending"},
                ],
                value="all",
                inline=True,
                style={"marginBottom": "15px"},
            ),
            dcc.Loading(
                id="loading-gaps-table",
                type="default",
                children=dash_table.DataTable(  # type: ignore[attr-defined]
                    id="gaps-detail-table",
                    columns=[],
                    data=[],
                    style_table={
                        "overflowX": "auto",
                        "maxHeight": "500px",
                        "overflowY": "auto",
                    },
                    style_header={
                        "backgroundColor": "#2c3e50",
                        "color": "white",
                        "fontWeight": "bold",
                        "textAlign": "left",
                    },
                    style_cell={
                        "textAlign": "left",
                        "padding": "8px",
                        "fontSize": "12px",
                    },
                    style_data_conditional=[
                        # Gaps complets (vert clair)
                        {
                            "if": {"filter_query": "{Statut} = 'complete'"},
                            "backgroundColor": "#d4edda",
                            "color": "black",
                        },
                        # Gaps partiels (jaune clair)
                        {
                            "if": {"filter_query": "{Statut} = 'partial'"},
                            "backgroundColor": "#fff3cd",
                            "color": "black",
                        },
                        # Gaps épuisés (rouge clair)
                        {
                            "if": {"filter_query": "{Statut} = 'all_methods_exhausted'"},
                            "backgroundColor": "#f8d7da",
                            "color": "black",
                        },
                        # Gaps en attente (bleu clair)
                        {
                            "if": {"filter_query": "{Statut} = 'pending'"},
                            "backgroundColor": "#d1ecf1",
                            "color": "black",
                        },
                    ],
                ),
            ),
        ], style={
            "padding": "20px",
            "border": "1px solid #ecf0f1",
            "borderRadius": "10px",
            "backgroundColor": "white",
            "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
        }),
    ])


def create_gap_summary_panel(data: dict) -> html.Div:
    """
    Crée le panneau de résumé global des gaps.

    Args:
        data: Données de gap_coverage (summary_stats, total_attempts, token_cashtag)

    Returns:
        html.Div avec le panneau de résumé
    """
    summary = data['summary_stats']
    token_cashtag = data['token_cashtag']
    total_attempts = data['total_attempts']

    # Calculer le pourcentage de complétude
    total_gaps = summary['total']
    complete_pct = (summary['complete'] / total_gaps * 100) if total_gaps > 0 else 0
    partial_pct = (summary['partial'] / total_gaps * 100) if total_gaps > 0 else 0
    exhausted_pct = (summary['exhausted'] / total_gaps * 100) if total_gaps > 0 else 0
    pending_pct = (summary['pending'] / total_gaps * 100) if total_gaps > 0 else 0

    # Couleur du panneau selon complétude moyenne
    avg_completeness = summary['avg_completeness']
    if avg_completeness >= 90:
        panel_color = "#d4edda"
        panel_border = "#c3e6cb"
    elif avg_completeness >= 70:
        panel_color = "#fff3cd"
        panel_border = "#ffeaa7"
    else:
        panel_color = "#f8d7da"
        panel_border = "#f5c6cb"

    return html.Div([
        html.H3(f"Résumé Global: {token_cashtag}", style={"color": "#2c3e50", "marginBottom": "15px"}),

        # Cartes de statistiques
        html.Div([
            # Carte 1: Total gaps
            html.Div([
                html.H4(str(total_gaps), style={"fontSize": "32px", "margin": "0", "color": "#3498db"}),
                html.P("Total Gaps", style={"margin": "5px 0", "color": "#7f8c8d"}),
            ], style={
                "flex": "1",
                "padding": "20px",
                "backgroundColor": "white",
                "borderRadius": "8px",
                "textAlign": "center",
                "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            }),

            # Carte 2: Gaps complets
            html.Div([
                html.H4(
                    f"{summary['complete']} ({complete_pct:.0f}%)",
                    style={"fontSize": "32px", "margin": "0", "color": "#27ae60"}
                ),
                html.P("Complets", style={"margin": "5px 0", "color": "#7f8c8d"}),
            ], style={
                "flex": "1",
                "padding": "20px",
                "backgroundColor": "white",
                "borderRadius": "8px",
                "textAlign": "center",
                "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            }),

            # Carte 3: Gaps partiels
            html.Div([
                html.H4(
                    f"{summary['partial']} ({partial_pct:.0f}%)",
                    style={"fontSize": "32px", "margin": "0", "color": "#f39c12"}
                ),
                html.P("Partiels", style={"margin": "5px 0", "color": "#7f8c8d"}),
            ], style={
                "flex": "1",
                "padding": "20px",
                "backgroundColor": "white",
                "borderRadius": "8px",
                "textAlign": "center",
                "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            }),

            # Carte 4: Gaps épuisés
            html.Div([
                html.H4(
                    f"{summary['exhausted']} ({exhausted_pct:.0f}%)",
                    style={"fontSize": "32px", "margin": "0", "color": "#e74c3c"}
                ),
                html.P("Épuisés", style={"margin": "5px 0", "color": "#7f8c8d"}),
            ], style={
                "flex": "1",
                "padding": "20px",
                "backgroundColor": "white",
                "borderRadius": "8px",
                "textAlign": "center",
                "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            }),

            # Carte 5: Complétude moyenne
            html.Div([
                html.H4(f"{avg_completeness:.1f}%", style={"fontSize": "32px", "margin": "0", "color": "#9b59b6"}),
                html.P("Complétude Moyenne", style={"margin": "5px 0", "color": "#7f8c8d"}),
            ], style={
                "flex": "1",
                "padding": "20px",
                "backgroundColor": "white",
                "borderRadius": "8px",
                "textAlign": "center",
                "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            }),

            # Carte 6: Tentatives totales
            html.Div([
                html.H4(str(total_attempts), style={"fontSize": "32px", "margin": "0", "color": "#34495e"}),
                html.P("Tentatives (7j)", style={"margin": "5px 0", "color": "#7f8c8d"}),
            ], style={
                "flex": "1",
                "padding": "20px",
                "backgroundColor": "white",
                "borderRadius": "8px",
                "textAlign": "center",
                "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
            }),
        ], style={
            "display": "flex",
            "gap": "15px",
            "flexWrap": "wrap",
        }),

    ], style={
        "padding": "20px",
        "backgroundColor": panel_color,
        "border": f"2px solid {panel_border}",
        "borderRadius": "10px",
    })


def create_scraping_methods_chart(scraping_stats_df: pd.DataFrame) -> go.Figure:
    """Crée un graphique des performances par méthode de scraping"""
    if scraping_stats_df.empty:
        return go.Figure()

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Taux de Succès par Méthode", "Tweets Récupérés par Méthode"),
        specs=[[{"type": "bar"}, {"type": "bar"}]],
    )

    # Graphique 1: Taux de succès
    fig.add_trace(
        go.Bar(
            x=scraping_stats_df['Méthode'],
            y=scraping_stats_df['Taux de Succès (%)'].str.rstrip('%').astype(float),
            name="Taux de Succès",
            marker_color='#3498db',
            text=scraping_stats_df['Taux de Succès (%)'],
            textposition='auto',
        ),
        row=1,
        col=1,
    )

    # Graphique 2: Tweets récupérés
    fig.add_trace(
        go.Bar(
            x=scraping_stats_df['Méthode'],
            y=scraping_stats_df['Tweets Récupérés'],
            name="Tweets",
            marker_color='#27ae60',
            text=scraping_stats_df['Tweets Récupérés'],
            textposition='auto',
        ),
        row=1,
        col=2,
    )

    fig.update_xaxes(title_text="Méthode", row=1, col=1)
    fig.update_xaxes(title_text="Méthode", row=1, col=2)
    fig.update_yaxes(title_text="Taux (%)", row=1, col=1)
    fig.update_yaxes(title_text="Nombre de Tweets", row=1, col=2)

    fig.update_layout(
        height=400,
        showlegend=False,
        template="plotly_white",
    )

    return fig


def create_nitter_instances_chart(nitter_stats_df: pd.DataFrame) -> go.Figure:
    """Crée un graphique des performances des instances Nitter"""
    if nitter_stats_df.empty:
        return go.Figure()

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=nitter_stats_df['Instance'],
        y=nitter_stats_df['Taux de Succès (%)'].str.rstrip('%').astype(float),
        name="Taux de Succès",
        marker_color='#9b59b6',
        text=nitter_stats_df['Taux de Succès (%)'],
        textposition='auto',
    ))

    fig.update_layout(
        title="Taux de Succès par Instance Nitter",
        xaxis_title="Instance",
        yaxis_title="Taux de Succès (%)",
        height=400,
        template="plotly_white",
    )

    return fig
