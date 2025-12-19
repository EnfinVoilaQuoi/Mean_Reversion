# src/database/dashboard_queries.py


from peewee import fn

from ..database.models import Token


def get_filtered_token_catalog(
    exchanges: list[str],
    search_query: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    Récupère une liste paginée de tokens basés sur les filtres de plateforme et la recherche.
    """
    query = (
        Token.select()
        .where(
            (Token.status == "MONITORING")
            | (Token.status == "OUT_OF_RANK")  # Inclure les déclassés si besoin
        )
        .order_by(Token.rank.asc())
    )

    # 1. FILTRE DE PLATEFORME (MEXC, ASTER, PIONEX)
    if exchanges:
        # Recherche les tokens dont la source de prix principale (primary_price_source)
        # est l'une des plateformes sélectionnées.
        query = query.where(Token.primary_price_source.in_(exchanges))

    # 2. FILTRE DE RECHERCHE MANUELLE (Symbole ou Cashtag)
    if search_query:
        # Utiliser LIKE pour la recherche partielle
        search_pattern = f"%{search_query.upper()}%"
        query = query.where(
            (fn.UPPER(Token.symbol).like(search_pattern))
            | (fn.UPPER(Token.cashtag).like(search_pattern))
        )

    # 3. PAGINATION et LIMITATION
    total_count = query.count()  # Compte le nombre total de résultats AVANT la limite
    results = query.limit(limit).offset(offset)

    # Convertir le résultat Peewee en liste de dictionnaires pour Dash
    data = list(results.dicts())

    return data, total_count
