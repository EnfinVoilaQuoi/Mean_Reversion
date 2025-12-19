"""
Callbacks pour l'onglet Gap Coverage du dashboard.
"""

import logging
from dash.dependencies import Input, Output

from .gap_coverage_tab import (
    fetch_gap_coverage_data,
    create_gap_summary_panel,
    create_scraping_methods_chart,
    create_nitter_instances_chart,
)

logger = logging.getLogger(__name__)


def register_gap_coverage_callbacks(app):
    """
    Enregistre tous les callbacks pour l'onglet Gap Coverage.

    Args:
        app: Instance de l'application Dash
    """

    # Callback 1: Mettre à jour le panneau de résumé
    @app.callback(
        Output("gap-summary-panel", "children"),
        Input("gap-token-selector", "value"),
    )
    def update_gap_summary(selected_token_id):
        """Met à jour le panneau de résumé global des gaps"""
        if selected_token_id is None:
            return ""

        try:
            data = fetch_gap_coverage_data(selected_token_id, days_back=7)
            return create_gap_summary_panel(data)
        except Exception as e:
            logger.error(f"Erreur update_gap_summary: {e}", exc_info=True)
            return ""

    # Callback 2: Graphique des méthodes de scraping
    @app.callback(
        Output("scraping-methods-chart", "figure"),
        Input("gap-token-selector", "value"),
    )
    def update_scraping_methods_chart(selected_token_id):
        """Met à jour le graphique des méthodes de scraping"""
        if selected_token_id is None:
            return {}

        try:
            data = fetch_gap_coverage_data(selected_token_id, days_back=7)
            return create_scraping_methods_chart(data['scraping_stats'])
        except Exception as e:
            logger.error(f"Erreur update_scraping_methods_chart: {e}", exc_info=True)
            return {}

    # Callback 3: Tableau des statistiques de scraping
    @app.callback(
        [
            Output("scraping-stats-table", "data"),
            Output("scraping-stats-table", "columns"),
        ],
        Input("gap-token-selector", "value"),
    )
    def update_scraping_stats_table(selected_token_id):
        """Met à jour le tableau des statistiques de scraping"""
        if selected_token_id is None:
            return [], []

        try:
            data = fetch_gap_coverage_data(selected_token_id, days_back=7)
            df = data['scraping_stats']

            if df.empty:
                return [], []

            columns = [{"name": col, "id": col} for col in df.columns]
            return df.to_dict('records'), columns
        except Exception as e:
            logger.error(f"Erreur update_scraping_stats_table: {e}", exc_info=True)
            return [], []

    # Callback 4: Graphique des instances Nitter
    @app.callback(
        Output("nitter-instances-chart", "figure"),
        Input("gap-token-selector", "value"),
    )
    def update_nitter_instances_chart(selected_token_id):
        """Met à jour le graphique des instances Nitter"""
        if selected_token_id is None:
            return {}

        try:
            data = fetch_gap_coverage_data(selected_token_id, days_back=7)
            return create_nitter_instances_chart(data['nitter_stats'])
        except Exception as e:
            logger.error(f"Erreur update_nitter_instances_chart: {e}", exc_info=True)
            return {}

    # Callback 5: Tableau des statistiques Nitter
    @app.callback(
        [
            Output("nitter-stats-table", "data"),
            Output("nitter-stats-table", "columns"),
        ],
        Input("gap-token-selector", "value"),
    )
    def update_nitter_stats_table(selected_token_id):
        """Met à jour le tableau des statistiques Nitter"""
        if selected_token_id is None:
            return [], []

        try:
            data = fetch_gap_coverage_data(selected_token_id, days_back=7)
            df = data['nitter_stats']

            if df.empty:
                return [], []

            columns = [{"name": col, "id": col} for col in df.columns]
            return df.to_dict('records'), columns
        except Exception as e:
            logger.error(f"Erreur update_nitter_stats_table: {e}", exc_info=True)
            return [], []

    # Callback 6: Tableau détaillé des gaps (avec filtre de statut)
    @app.callback(
        [
            Output("gaps-detail-table", "data"),
            Output("gaps-detail-table", "columns"),
        ],
        [
            Input("gap-token-selector", "value"),
            Input("gap-status-filter", "value"),
        ],
    )
    def update_gaps_detail_table(selected_token_id, status_filter):
        """Met à jour le tableau détaillé des gaps"""
        if selected_token_id is None:
            return [], []

        try:
            data = fetch_gap_coverage_data(selected_token_id, days_back=7)
            df = data['gaps_summary']

            if df.empty:
                return [], []

            # Appliquer le filtre de statut
            if status_filter != "all":
                df = df[df['Statut'] == status_filter]

            columns = [{"name": col, "id": col} for col in df.columns]
            return df.to_dict('records'), columns
        except Exception as e:
            logger.error(f"Erreur update_gaps_detail_table: {e}", exc_info=True)
            return [], []

    # Callback 7: Synchroniser gap-token-selector avec token-selector
    @app.callback(
        Output("gap-token-selector", "options"),
        Input("token-selector", "options"),
    )
    def sync_gap_token_selector(token_options):
        """Synchronise les options du sélecteur de token pour Gap Coverage"""
        return token_options

    logger.info("✅ Callbacks Gap Coverage enregistrés")
