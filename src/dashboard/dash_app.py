"""
Dashboard Dash - Interface Web avec contrôle complet du système
Version Améliorée avec tableau de bord et panneau de contrôle
"""

import logging
from datetime import datetime, timedelta

import dash
import pandas as pd
import plotly.express as px
from dash import dash_table, dcc, html
from dash.dependencies import Input, Output, State

# Imports de l'application
from ..database.models import PriceMetric, SignalMetric, Token, ScrapedGap, GapAttempt
from ..analysis.gap_tracking import get_scraping_summary

logger = logging.getLogger(__name__)

# Référence globale à la fonction update_monitoring_job (sera injectée depuis main.py)
_update_monitoring_job_func = None


def set_main_functions(update_job_func):
    """
    Injecte la fonction update_monitoring_job depuis main.py.
    À appeler avant de démarrer le dashboard.

    Args:
        update_job_func: Fonction update_monitoring_job
    """
    global _update_monitoring_job_func
    _update_monitoring_job_func = update_job_func
    logger.info("✅ Fonction update_monitoring_job injectée dans dash_app.py")


def update_monitoring_job(token, mode):
    """Wrapper pour appeler la fonction injectée"""
    if _update_monitoring_job_func:
        return _update_monitoring_job_func(token, mode)
    logger.error("❌ update_monitoring_job non disponible.")
    return False


# Démarrage de l'application Dash
app = dash.Dash(__name__)

# Mapping des modes aux fréquences pour l'affichage
MODE_FREQUENCIES = {
    "AGGRESSIVE": "5 min",
    "REGULAR": "15 min",
    "CROISIERE": "60 min",
    "PENDING": "Backfill",
}

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================


def get_all_token_statuses() -> pd.DataFrame:
    """Récupère l'état actuel des tokens actifs dans la DB."""

    data = []
    # Sélectionner uniquement les tokens actifs
    for token in Token.select().where(Token.status.in_(['CROISIERE', 'REGULAR', 'AGGRESSIVE'])):
        # Récupérer la dernière métrique depuis SignalMetric (Architecture 3NF)
        last_signal = (
            SignalMetric.select()
            .where(SignalMetric.token == token.id)
            .order_by(SignalMetric.timestamp.desc())
            .limit(1)
            .get_or_none()
        )

        # Extraire les données depuis SignalMetric
        if last_signal:
            price = last_signal.price_metric.close if last_signal.price_metric else None
            z_social = last_signal.z_score_social
            z_price = last_signal.z_score_price
            divergence = last_signal.divergence_score
            timestamp = last_signal.timestamp
        else:
            # Aucune métrique disponible
            price = None
            z_social = None
            z_price = None
            divergence = None
            timestamp = None

        data.append(
            {
                "ID": token.id,
                "Token": token.cashtag,
                "Symbole": token.symbol,
                "Rank": token.rank or "N/A",
                "Rank Status": token.rank_status,
                "Status": token.status,
                "Fréquence": MODE_FREQUENCIES.get(token.status, "N/A"),
                "Dernier Prix": f"${price:.6f}" if price else "N/A",
                "Z-Social": f"{z_social:.2f}" if z_social is not None else "N/A",
                "Z-Prix": f"{z_price:.2f}" if z_price is not None else "N/A",
                "Divergence": f"{divergence:+.2f}" if divergence is not None else "N/A",
                "Dernière MAJ": timestamp.strftime("%H:%M:%S") if timestamp else "N/A",
            }
        )

    return pd.DataFrame(data)


def fetch_token_data(token_id: int, hours_back: int = 120) -> pd.DataFrame:
    """Récupère les métriques pour un token donné (5 jours par défaut)."""
    cutoff = datetime.now() - timedelta(hours=hours_back)

    # Récupération du token
    token = Token.get_by_id(token_id)

    # Architecture 3NF: Récupération depuis SignalMetric uniquement
    signal_metrics = (
        SignalMetric.select()
        .where((SignalMetric.token == token) & (SignalMetric.timestamp >= cutoff))
        .order_by(SignalMetric.timestamp.asc())
    )

    # Récupération des données historiques de prix (PriceMetric) pour combler les trous Z-Price
    price_metrics = (
        PriceMetric.select()
        .where(
            (PriceMetric.token == token)
            & (PriceMetric.timestamp >= cutoff)
            & (PriceMetric.z_score_price.is_null(False))
        )
        .order_by(PriceMetric.timestamp.asc())
    )

    # Fusion des données
    data = []

    # 1. Ajouter les métriques depuis SignalMetric (Architecture 3NF)
    for sig in signal_metrics:
        # Récupérer les données via ForeignKeys
        social = sig.social_metric
        price_m = sig.price_metric

        price = price_m.close if price_m else None
        z_price = sig.z_score_price

        data.append(
            {
                "Timestamp": sig.timestamp,
                "Prix": price,
                "Z_Social": sig.z_score_social,
                "Z_Activity": sig.z_score_vs_activity,  # Dual Z-Score
                "Z_Price": z_price,
                "Divergence": sig.divergence_score,
                "Social_Volume": social.social_volume if social else None,
                "Social_Density": social.social_density if social else None,
                "Trading_Vol_24h": price_m.volume if price_m else None,
                "Type": "Signal",
            }
        )

    # 2. Ajouter les métriques de prix (Pour l'historique Z-Price complet)
    for m in price_metrics:
        data.append(
            {
                "Timestamp": m.timestamp,
                "Prix": m.close,
                "Z_Social": None,
                "Z_Activity": None,  # Pas de dual Z-Score pour métriques prix seules
                "Z_Price": m.z_score_price,
                "Divergence": None,
                "Social_Volume": None,
                "Social_Density": None,
                "Trading_Vol_24h": None,
                "Type": "Price",
            }
        )

    df = pd.DataFrame(data)

    if not df.empty:
        df = df.sort_values("Timestamp")

    return df


def fetch_token_social_details(token_id: int, hours_back: int = 168) -> pd.DataFrame:
    """
    Récupère les métriques détaillées du Z-Score Social (7 derniers jours).

    Retourne un DataFrame avec toutes les données agrégées pour visualisation.
    """
    cutoff = datetime.now() - timedelta(hours=hours_back)

    token = Token.get_by_id(token_id)

    # Architecture 3NF: Utiliser SignalMetric avec JOIN
    signals = (
        SignalMetric.select()
        .where((SignalMetric.token == token) & (SignalMetric.timestamp >= cutoff))
        .order_by(SignalMetric.timestamp.asc())
    )

    data = []
    for sig in signals:
        # Récupérer les données via ForeignKeys
        social = sig.social_metric
        price_m = sig.price_metric

        data.append(
            {
                "Timestamp": sig.timestamp,
                "Heure": sig.timestamp.strftime("%Y-%m-%d %H:%M"),
                "Z_Social": sig.z_score_social or 0.0,
                "Z_Activity": sig.z_score_vs_activity or 0.0,  # NOUVEAU: Dual Z-Score
                "Social_Volume": social.social_volume if social else 0.0,
                "Social_Density": social.social_density if social else 0.0,
                "Trading_Vol_24h": price_m.volume if price_m else 0.0,
                "Prix": price_m.close if price_m else 0.0,
                # Champs d'imputation pour traçabilité
                "Is_Imputed": social.is_imputed if social else False,
                "Imputation_Method": social.imputation_method if social else None,
                # Champs de validation (Dual Z-Score System)
                "Is_Valid": social.is_valid if social else True,  # NOUVEAU
                "Data_Quality": social.data_quality if social else "unknown",  # NOUVEAU
            }
        )

    return pd.DataFrame(data)


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
        - timeline_data: Données pour timeline visuelle
    """
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

    # 6. Données pour timeline (derniers jours_back jours)
    cutoff = datetime.now() - timedelta(days=days_back)
    timeline_gaps = [g for g in gaps if g.last_attempt and g.last_attempt >= cutoff]

    return {
        'gaps_summary': gaps_df,
        'scraping_stats': scraping_stats_df,
        'nitter_stats': nitter_stats_df,
        'summary_stats': summary['gaps'],
        'total_attempts': summary['total_attempts'],
        'token_cashtag': token.cashtag,
    }


# ============================================================================
# LAYOUT DU DASHBOARD
# ============================================================================

app.layout = html.Div(
    [
        # Header
        html.Div(
            [
                html.H1(
                    "🚀 Social Divergence Live Monitor",
                    style={"textAlign": "center", "color": "#2c3e50"},
                ),
                html.P(
                    "Dashboard de contrôle et surveillance en temps réel",
                    style={"textAlign": "center", "color": "#7f8c8d"},
                ),
            ],
            style={
                "padding": "20px",
                "backgroundColor": "#ecf0f1",
                "marginBottom": "20px",
            },
        ),
        # Intervalle de rafraîchissement automatique
        dcc.Interval(
            id="interval-component",
            interval=60 * 1000,  # 1 minute (60 secondes)
            n_intervals=0,
        ),
        # Tabs pour organiser le dashboard
        dcc.Tabs(
            id="main-tabs",
            value="monitoring-tab",
            children=[
                dcc.Tab(label="📊 Monitoring & Contrôle", value="monitoring-tab"),
                dcc.Tab(label="📈 Analyse Divergence", value="analysis-tab"),
                dcc.Tab(label="🔍 Gap Coverage", value="gap-coverage-tab"),
            ],
            style={"marginBottom": "20px"},
        ),
        # Container principal avec contenu dynamique selon l'onglet
        html.Div(id="tab-content", style={"maxWidth": "1400px", "margin": "0 auto", "padding": "20px"}),
        # Footer
        html.Div(
            [
                html.P(
                    f"Dernière mise à jour : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    id="last-update",
                    style={
                        "textAlign": "center",
                        "color": "#95a5a6",
                        "marginTop": "20px",
                        "fontSize": "12px",
                    },
                )
            ]
        ),
    ],
    style={"backgroundColor": "#f5f6fa", "minHeight": "100vh"},
)


# ============================================================================
# COMPOSANTS DE TABS
# ============================================================================


def create_monitoring_tab():
    """Crée le contenu de l'onglet Monitoring & Contrôle"""
    return html.Div(
            [
                # ====================================================================
                # ZONE 1: TABLEAU DE SUIVI DES TOKENS ET CONTRÔLE
                # ====================================================================
                html.Div(
                    [
                        html.H2(
                            "📋 Liste des Tokens Surveillés",
                            style={"color": "#34495e", "marginBottom": "15px"},
                        ),
                        html.P(
                            "Rafraîchissement automatique: 1 minute",
                            style={"color": "#7f8c8d", "fontSize": "14px"},
                        ),
                        # Le tableau de données
                        dash_table.DataTable(  # type: ignore[attr-defined]
                            id="token-status-table",
                            columns=[
                                {"name": "Token", "id": "Token", "type": "text"},
                                {"name": "Symbole", "id": "Symbole", "type": "text"},
                                {"name": "Status", "id": "Status", "type": "text"},
                                {
                                    "name": "Fréquence",
                                    "id": "Fréquence",
                                    "type": "text",
                                },
                                {
                                    "name": "Dernier Prix",
                                    "id": "Dernier Prix",
                                    "type": "text",
                                },
                                {"name": "Z-Social", "id": "Z-Social", "type": "text"},
                                {"name": "Z-Prix", "id": "Z-Prix", "type": "text"},
                                {
                                    "name": "Divergence",
                                    "id": "Divergence",
                                    "type": "text",
                                },
                                {
                                    "name": "Dernière MAJ",
                                    "id": "Dernière MAJ",
                                    "type": "text",
                                },
                            ],
                            data=[],  # Données vides au démarrage, seront remplies par le callback
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
                            style_data_conditional=[
                                # Mode AGGRESSIVE = Rose
                                {
                                    "if": {"filter_query": '{Status} = "AGGRESSIVE"'},
                                    "backgroundColor": "#FFC0CB",
                                    "color": "black",
                                },
                                # Mode PENDING = Jaune
                                {
                                    "if": {"filter_query": '{Status} = "PENDING"'},
                                    "backgroundColor": "#FFFFE0",
                                    "color": "black",
                                },
                                # Mode CROISIERE = Lavande
                                {
                                    "if": {"filter_query": '{Status} = "CROISIERE"'},
                                    "backgroundColor": "#E6E6FA",
                                    "color": "black",
                                },
                                # Mode REGULAR = Blanc (par défaut)
                                {
                                    "if": {"filter_query": '{Status} = "REGULAR"'},
                                    "backgroundColor": "white",
                                    "color": "black",
                                },
                                # Divergence positive (rouge clair)
                                {
                                    "if": {"filter_query": '{Divergence} > "+2.0"'},
                                    "backgroundColor": "#ffdddd",
                                },
                                # Divergence négative (vert clair)
                                {
                                    "if": {"filter_query": '{Divergence} < "-2.0"'},
                                    "backgroundColor": "#ddffdd",
                                },
                            ],
                            row_selectable="single",
                            selected_rows=[],
                        ),
                        # Panneau de Contrôle de la Fréquence
                        html.Div(
                            [
                                html.H3(
                                    "⚙️ Changer Fréquence de Surveillance",
                                    style={"color": "#2c3e50", "marginBottom": "15px"},
                                ),
                                html.P(
                                    "Sélectionnez une ligne dans le tableau ci-dessus puis choisissez une fréquence :",
                                    style={"color": "#7f8c8d", "fontSize": "14px"},
                                ),
                                dcc.RadioItems(
                                    id="frequency-radio",
                                    options=[  # type: ignore[arg-type]
                                        {
                                            "label": " 🔵 CROISIERE (60 min)",
                                            "value": "CROISIERE",
                                        },
                                        {
                                            "label": " 🟢 REGULAR (15 min)",
                                            "value": "REGULAR",
                                        },
                                        {
                                            "label": " 🔥 AGGRESSIVE (5 min)",
                                            "value": "AGGRESSIVE",
                                        },
                                    ],
                                    value="REGULAR",
                                    inline=True,
                                    style={"marginBottom": "15px", "fontSize": "14px"},
                                ),
                                html.Button(
                                    "Appliquer la Nouvelle Fréquence",
                                    id="set-mode-button",
                                    n_clicks=0,
                                    style={
                                        "marginTop": "10px",
                                        "backgroundColor": "#27ae60",
                                        "color": "white",
                                        "padding": "12px 20px",
                                        "border": "none",
                                        "borderRadius": "5px",
                                        "cursor": "pointer",
                                        "fontSize": "14px",
                                        "fontWeight": "bold",
                                    },
                                ),
                                html.Div(
                                    id="mode-change-output",
                                    style={
                                        "marginTop": "15px",
                                        "padding": "10px",
                                        "borderRadius": "5px",
                                        "fontSize": "14px",
                                    },
                                ),
                            ],
                            style={
                                "border": "1px solid #bdc3c7",
                                "padding": "20px",
                                "marginTop": "20px",
                                "borderRadius": "8px",
                                "backgroundColor": "#f8f9fa",
                            },
                        ),
                    ],
                    style={
                        "padding": "20px",
                        "border": "1px solid #ecf0f1",
                        "borderRadius": "10px",
                        "marginBottom": "30px",
                        "backgroundColor": "white",
                        "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
                    },
                ),
                html.Hr(),
                # ====================================================================
                # ZONE 2: GRAPHIQUE EN TEMPS RÉEL
                # ====================================================================
                html.Div(
                    [
                        html.H2(
                            "📈 Graphique d'Analyse de Divergence",
                            style={"color": "#34495e", "marginBottom": "15px"},
                        ),
                        dcc.Dropdown(
                            id="token-selector",
                            options=[],  # Options vides au démarrage, seront remplies par le callback
                            value=None,
                            placeholder="Sélectionner un Token à surveiller pour le graphique",
                        ),
                        dcc.Loading(
                            id="loading-output",
                            type="default",
                            children=dcc.Graph(id="live-update-graph"),
                        ),
                    ],
                    style={
                        "padding": "20px",
                        "border": "1px solid #ecf0f1",
                        "borderRadius": "10px",
                        "backgroundColor": "white",
                        "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
                    },
                ),
                html.Hr(),
                # ====================================================================
                # ZONE 3: ANALYSE DÉTAILLÉE DU Z-SCORE SOCIAL
                # ====================================================================
                html.Div(
                    [
                        html.H2(
                            "📊 Analyse Détaillée du Z-Score Social (7 jours)",
                            style={"color": "#34495e", "marginBottom": "15px"},
                        ),
                        html.P(
                            "Évolution heure par heure avec métriques agrégées",
                            style={"color": "#7f8c8d", "fontSize": "14px"},
                        ),
                        dcc.Loading(
                            id="loading-social-graph",
                            type="default",
                            children=[
                                # Graphique Z-Score Social
                                dcc.Graph(id="zscore-social-graph"),
                                # Graphique des métriques sociales
                                dcc.Graph(id="social-metrics-graph"),
                                # Tableau détaillé (dernières 24h)
                                html.H3(
                                    "📋 Dernières 24 heures - Détails",
                                    style={
                                        "color": "#34495e",
                                        "marginTop": "30px",
                                        "marginBottom": "15px",
                                    },
                                ),
                                dash_table.DataTable(  # type: ignore[attr-defined]
                                    id="social-details-table",
                                    columns=[
                                        {"name": "Heure", "id": "Heure"},
                                        {
                                            "name": "Z-Social",
                                            "id": "Z_Social",
                                            "type": "numeric",
                                            "format": {"specifier": ".2f"},
                                        },
                                        {
                                            "name": "Z-Activity",
                                            "id": "Z_Activity",
                                            "type": "numeric",
                                            "format": {"specifier": ".2f"},
                                        },
                                        {
                                            "name": "Social Volume",
                                            "id": "Social_Volume",
                                            "type": "numeric",
                                            "format": {"specifier": ".2f"},
                                        },
                                        {
                                            "name": "Social Density",
                                            "id": "Social_Density",
                                            "type": "numeric",
                                            "format": {"specifier": ".2f"},
                                        },
                                        {
                                            "name": "Vol 24h (USD)",
                                            "id": "Trading_Vol_24h",
                                            "type": "numeric",
                                            "format": {"specifier": ",.0f"},
                                        },
                                        {
                                            "name": "Prix (USD)",
                                            "id": "Prix",
                                            "type": "numeric",
                                            "format": {"specifier": ".6f"},
                                        },
                                        {
                                            "name": "Valide",
                                            "id": "Is_Valid",
                                            "type": "text",
                                        },
                                        {
                                            "name": "Qualité",
                                            "id": "Data_Quality",
                                            "type": "text",
                                        },
                                        {
                                            "name": "Imputé",
                                            "id": "Is_Imputed",
                                            "type": "text",
                                        },
                                        {
                                            "name": "Méthode",
                                            "id": "Imputation_Method",
                                            "type": "text",
                                        },
                                    ],
                                    data=[],
                                    style_table={
                                        "overflowX": "auto",
                                        "maxHeight": "400px",
                                        "overflowY": "auto",
                                    },
                                    style_header={
                                        "backgroundColor": "#9b59b6",
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
                                        # Données imputées (fond jaune clair)
                                        {
                                            "if": {
                                                "filter_query": "{Is_Imputed} = 'Oui'",
                                            },
                                            "backgroundColor": "#fff9e6",
                                            "fontStyle": "italic",
                                        },
                                        # Données invalides (fond rouge clair)
                                        {
                                            "if": {
                                                "filter_query": "{Is_Valid} = 'Non'",
                                            },
                                            "backgroundColor": "#ffdddd",
                                            "fontStyle": "italic",
                                        },
                                        # Data Quality: high (vert)
                                        {
                                            "if": {
                                                "filter_query": "{Data_Quality} = 'high'",
                                                "column_id": "Data_Quality",
                                            },
                                            "color": "#27ae60",
                                            "fontWeight": "bold",
                                        },
                                        # Data Quality: uncertain (orange)
                                        {
                                            "if": {
                                                "filter_query": "{Data_Quality} = 'uncertain'",
                                                "column_id": "Data_Quality",
                                            },
                                            "color": "#f39c12",
                                            "fontWeight": "bold",
                                        },
                                        # Data Quality: low (rouge)
                                        {
                                            "if": {
                                                "filter_query": "{Data_Quality} = 'low'",
                                                "column_id": "Data_Quality",
                                            },
                                            "color": "#e74c3c",
                                            "fontWeight": "bold",
                                        },
                                        # Z-Social positif (hype)
                                        {
                                            "if": {
                                                "filter_query": "{Z_Social} > 2.0",
                                                "column_id": "Z_Social",
                                            },
                                            "backgroundColor": "#ffcccc",
                                            "color": "black",
                                            "fontWeight": "bold",
                                        },
                                        # Z-Social négatif (calme)
                                        {
                                            "if": {
                                                "filter_query": "{Z_Social} < -2.0",
                                                "column_id": "Z_Social",
                                            },
                                            "backgroundColor": "#ccffcc",
                                            "color": "black",
                                            "fontWeight": "bold",
                                        },
                                        # Z-Activity positif (hype réel)
                                        {
                                            "if": {
                                                "filter_query": "{Z_Activity} > 2.0",
                                                "column_id": "Z_Activity",
                                            },
                                            "backgroundColor": "#ffcccc",
                                            "color": "black",
                                            "fontWeight": "bold",
                                        },
                                        # Z-Activity négatif (calme réel)
                                        {
                                            "if": {
                                                "filter_query": "{Z_Activity} < -2.0",
                                                "column_id": "Z_Activity",
                                            },
                                            "backgroundColor": "#ccffcc",
                                            "color": "black",
                                            "fontWeight": "bold",
                                        },
                                    ],
                                ),
                            ],
                        ),
                    ],
                    style={
                        "padding": "20px",
                        "border": "1px solid #ecf0f1",
                        "borderRadius": "10px",
                        "backgroundColor": "white",
                        "boxShadow": "0 2px 4px rgba(0,0,0,0.1)",
                        "marginTop": "20px",
                    },
                ),
            ],
            style={"maxWidth": "1400px", "margin": "0 auto", "padding": "20px"},
        )


# ============================================================================
# CALLBACKS
# ============================================================================


# Callback 0: Charger le contenu des onglets
@app.callback(
    Output("tab-content", "children"),
    Input("main-tabs", "value")
)
def render_tab_content(active_tab):
    """Charge le contenu de l'onglet sélectionné"""
    if active_tab == "monitoring-tab":
        return create_monitoring_tab()
    elif active_tab == "analysis-tab":
        return html.Div([
            html.H2("📈 Analyse de Divergence", style={"textAlign": "center"}),
            html.P("Fonctionnalité en développement", style={"textAlign": "center", "color": "#7f8c8d"})
        ])
    elif active_tab == "gap-coverage-tab":
        return html.Div([
            html.H2("🔍 Gap Coverage", style={"textAlign": "center"}),
            html.P("Fonctionnalité en développement", style={"textAlign": "center", "color": "#7f8c8d"})
        ])
    return html.Div([html.P("Onglet non reconnu")])


# Callback 1: Rafraîchir le tableau des statuts
@app.callback(
    [
        Output("token-status-table", "data"),
        Output("token-selector", "options"),
        Output("last-update", "children"),
    ],
    [
        Input("interval-component", "n_intervals"),
        Input("mode-change-output", "children"),
    ],  # Se déclenche aussi après un changement de mode
)
def refresh_token_table(n_intervals, mode_change_message):
    """Rafraîchit le tableau et le dropdown des tokens"""
    df = get_all_token_statuses()
    options = [
        {"label": f"{row['Token']} ({row['Symbole']})", "value": row["ID"]}
        for _, row in df.iterrows()
    ]
    last_update = (
        f"Dernière mise à jour : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return df.to_dict("records"), options, last_update


# Callback 2: Gérer le changement de mode
@app.callback(
    Output("mode-change-output", "children"),
    Input("set-mode-button", "n_clicks"),
    State("token-status-table", "selected_rows"),
    State("token-status-table", "data"),
    State("frequency-radio", "value"),
    prevent_initial_call=True,
)
def change_token_mode(n_clicks, selected_rows, rows_data, new_mode):
    """Change le mode de surveillance d'un token"""
    if not selected_rows:
        return html.Div(
            "⚠️ Veuillez sélectionner un token dans la table avant de changer le mode.",
            style={
                "backgroundColor": "#fff3cd",
                "color": "#856404",
                "padding": "10px",
                "borderRadius": "5px",
            },
        )

    # Récupérer l'ID du token sélectionné
    selected_row_index = selected_rows[0]
    token_id = rows_data[selected_row_index]["ID"]
    symbol = rows_data[selected_row_index]["Token"]

    try:
        # 1. Mise à jour de la DB
        token = Token.get_by_id(token_id)
        old_mode = token.status

        # 2. Mise à jour du Scheduler (dans main.py via la fonction importée)
        update_monitoring_job(token, new_mode.lower())

        # 3. Mise à jour du statut dans la DB
        token.status = new_mode
        token.save()

        logger.info(f"✅ Mode changé pour {symbol}: {old_mode} → {new_mode}")

        return html.Div(
            f"✅ {symbol} est passé de {old_mode} à {new_mode} ({MODE_FREQUENCIES[new_mode]}).",
            style={
                "backgroundColor": "#d4edda",
                "color": "#155724",
                "padding": "10px",
                "borderRadius": "5px",
            },
        )

    except Token.DoesNotExist:
        return html.Div(
            f"❌ Erreur: Token ID {token_id} introuvable.",
            style={
                "backgroundColor": "#f8d7da",
                "color": "#721c24",
                "padding": "10px",
                "borderRadius": "5px",
            },
        )
    except Exception as e:
        logger.error(f"❌ Erreur lors de la mise à jour du mode: {e}", exc_info=True)
        return html.Div(
            f"❌ Erreur critique: {str(e)}",
            style={
                "backgroundColor": "#f8d7da",
                "color": "#721c24",
                "padding": "10px",
                "borderRadius": "5px",
            },
        )


# Callback 3: Mise à jour du graphique en temps réel
@app.callback(
    Output("live-update-graph", "figure"),
    [Input("token-selector", "value"), Input("interval-component", "n_intervals")],
)
def update_graph_live(selected_token_id, n):
    """Met à jour le graphique de divergence"""
    if selected_token_id is None:
        return {}

    df = fetch_token_data(selected_token_id)

    if df.empty:
        return {}

    try:
        token = Token.get_by_id(selected_token_id)

        # Création de la figure Plotly
        fig = px.line(
            df,
            x="Timestamp",
            y=["Z_Social", "Z_Activity", "Z_Price", "Divergence"],
            title=f"Analyse de Divergence Z-Score pour {token.cashtag}",
        )

        # Activer la connexion des points pour éviter les trous dus aux NaNs (surtout pour Z-Price fusionné)
        fig.update_traces(connectgaps=True)

        # Ajouter le prix sur un axe secondaire
        fig.add_scatter(
            x=df["Timestamp"],
            y=df["Prix"],
            name="Prix Actuel",
            yaxis="y2",
            line={"color": "gray", "dash": "dot"},
            connectgaps=True,  # Relier les points manquants pour le prix aussi
        )

        fig.update_layout(
            xaxis_title="Temps",
            yaxis_title="Z-Scores / Divergence",
            yaxis2={"title": "Prix ($)", "overlaying": "y", "side": "right"},
            legend_title="Métriques",
            height=600,
            hovermode="x unified",
            template="plotly_white",
        )

        # Appliquer connectgaps à toutes les traces (sécurité)
        fig.update_traces(connectgaps=True)

        return fig

    except Exception as e:
        logger.error(f"❌ Erreur update_graph_live: {e}")
        return {}


# Callback 4: Graphique Z-Score Social détaillé
@app.callback(
    Output("zscore-social-graph", "figure"),
    [Input("token-selector", "value"), Input("interval-component", "n_intervals")],
)
def update_zscore_social_graph(selected_token_id, n):
    """Met à jour le graphique du Z-Score Social sur 7 jours"""
    if selected_token_id is None:
        return {}

    df = fetch_token_social_details(selected_token_id, hours_back=168)  # 7 jours

    if df.empty:
        return {}

    try:
        token = Token.get_by_id(selected_token_id)

        # Créer le graphique avec zones colorées
        import plotly.graph_objects as go

        fig = go.Figure()

        # Ligne du Z-Score Social
        fig.add_trace(
            go.Scatter(
                x=df["Timestamp"],
                y=df["Z_Social"],
                mode="lines+markers",
                name="Z-Score Social (complet)",
                line={"color": "#3498db", "width": 2},
                marker={"size": 4},
            )
        )

        # Ligne du Z-Score vs Activity (filtré epsilon)
        fig.add_trace(
            go.Scatter(
                x=df["Timestamp"],
                y=df["Z_Activity"],
                mode="lines+markers",
                name="Z-Score vs Activity (filtré)",
                line={"color": "#9b59b6", "width": 2, "dash": "dot"},
                marker={"size": 4},
            )
        )

        # Zones de référence
        fig.add_hrect(
            y0=2,
            y1=100,
            fillcolor="rgba(255,0,0,0.1)",
            line_width=0,
            annotation_text="Hype Anormale",
            annotation_position="top left",
        )
        fig.add_hrect(
            y0=-2,
            y1=-100,
            fillcolor="rgba(0,255,0,0.1)",
            line_width=0,
            annotation_text="Désintérêt",
            annotation_position="bottom left",
        )
        fig.add_hline(
            y=0, line_dash="dash", line_color="gray", annotation_text="Neutre"
        )

        fig.update_layout(
            title=f"Évolution du Z-Score Social - {token.cashtag} (7 jours)",
            xaxis_title="Temps",
            yaxis_title="Z-Score Social",
            height=400,
            hovermode="x unified",
            template="plotly_white",
            showlegend=True,
        )

        return fig

    except Exception as e:
        logger.error(f"❌ Erreur update_zscore_social_graph: {e}")
        return {}


# Callback 5: Graphique des métriques sociales
@app.callback(
    Output("social-metrics-graph", "figure"),
    [Input("token-selector", "value"), Input("interval-component", "n_intervals")],
)
def update_social_metrics_graph(selected_token_id, n):
    """Met à jour le graphique des métriques sociales (volume, density)"""
    if selected_token_id is None:
        return {}

    df = fetch_token_social_details(selected_token_id, hours_back=168)

    if df.empty:
        return {}

    try:
        token = Token.get_by_id(selected_token_id)

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        # Créer un graphique avec 2 sous-graphiques
        fig = make_subplots(
            rows=2,
            cols=1,
            subplot_titles=("Social Volume", "Social Density"),
            vertical_spacing=0.12,
            row_heights=[0.5, 0.5],
        )

        # Graphique 1: Social Volume
        fig.add_trace(
            go.Scatter(
                x=df["Timestamp"],
                y=df["Social_Volume"],
                mode="lines",
                name="Social Volume",
                line={"color": "#9b59b6", "width": 2},
                fill="tozeroy",
            ),
            row=1,
            col=1,
        )

        # Graphique 2: Social Density
        fig.add_trace(
            go.Scatter(
                x=df["Timestamp"],
                y=df["Social_Density"],
                mode="lines",
                name="Social Density",
                line={"color": "#e74c3c", "width": 2},
                fill="tozeroy",
            ),
            row=2,
            col=1,
        )

        fig.update_xaxes(title_text="Temps", row=2, col=1)
        fig.update_yaxes(title_text="Volume", row=1, col=1)
        fig.update_yaxes(title_text="Densité", row=2, col=1)

        fig.update_layout(
            title_text=f"Métriques Sociales Agrégées - {token.cashtag}",
            height=600,
            hovermode="x unified",
            template="plotly_white",
            showlegend=False,
        )

        return fig

    except Exception as e:
        logger.error(f"❌ Erreur update_social_metrics_graph: {e}")
        return {}


# Callback 6: Tableau détaillé des dernières 24h
@app.callback(
    Output("social-details-table", "data"),
    [Input("token-selector", "value"), Input("interval-component", "n_intervals")],
)
def update_social_details_table(selected_token_id, n):
    """Met à jour le tableau détaillé des 72 dernières heures"""
    if selected_token_id is None:
        return []

    df = fetch_token_social_details(selected_token_id, hours_back=72)

    if df.empty:
        return []

    # Trier par ordre décroissant (plus récent en premier)
    df = df.sort_values("Timestamp", ascending=False)

    # Formater les champs d'imputation et validation pour l'affichage
    df["Is_Imputed"] = df["Is_Imputed"].apply(lambda x: "Oui" if x else "Non")
    df["Is_Valid"] = df["Is_Valid"].apply(lambda x: "Oui" if x else "Non")
    df["Imputation_Method"] = df["Imputation_Method"].fillna("-")
    df["Data_Quality"] = df["Data_Quality"].fillna("unknown")

    return df.to_dict("records")


# ============================================================================
# FONCTION DE DÉMARRAGE
# ============================================================================


def run_dashboard(host="0.0.0.0", port=8050, debug=False):
    """
    Démarre l'application Dash (fonction bloquante).

    Args:
        host (str): Adresse d'écoute (0.0.0.0 = accessible depuis le réseau)
        port (int): Port d'écoute
        debug (bool): Mode debug (auto-reload, à éviter en production)
    """
    try:
        logger.info(f"🚀 Démarrage du Dashboard Dash sur http://{host}:{port}")
        # use_reloader=False est CRUCIAL pour éviter le double démarrage en mode thread
        app.run(debug=debug, host=host, port=port, use_reloader=False)
    except Exception as e:
        logger.error(f"❌ Erreur fatale du Dashboard Dash: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    # Test standalone
    run_dashboard(debug=True)
