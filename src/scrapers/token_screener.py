"""
Token Screener - Module de screening des tokens par MarketCap
Récupère le Top X tokens depuis CoinGecko et trouve la meilleure paire liquide
Utilise api_manager pour le rate limiting automatique
"""

import contextlib
import json
import logging
from datetime import datetime
from typing import Any

from ..config import EXTERNAL_APIS, SCREENING_CONFIG
from ..database.models import Token, db
from ..utils.api_manager import api_manager

logger = logging.getLogger(__name__)

# Flag d'arrêt global (pour Ctrl+C)
_shutdown_requested = False


def request_shutdown():
    """Demande l'arrêt gracieux du screening en cours"""
    global _shutdown_requested
    _shutdown_requested = True
    logger.warning("🛑 Arrêt du screening demandé...")


def is_shutdown_requested() -> bool:
    """Vérifie si l'arrêt a été demandé"""
    return _shutdown_requested


# Configuration du screening
COINGECKO_BASE: str = EXTERNAL_APIS["COINGECKO_BASE"]
TOP_LIMIT: int = SCREENING_CONFIG["TOP_LIMIT"]
TARGET_EXCHANGES: list[str] = SCREENING_CONFIG["TARGET_EXCHANGES"]
EXCHANGE_PRIORITY_ORDER: list[str] = SCREENING_CONFIG[
    "EXCHANGE_PRIORITY_ORDER"
]  # Ordre de priorité
TARGET_QUOTE_ASSETS: list[str] = SCREENING_CONFIG["TARGET_QUOTE_ASSETS"]
IGNORE_CHAINS: list[str] = SCREENING_CONFIG["IGNORE_CHAINS"]
REQUEST_DELAY: float = SCREENING_CONFIG["REQUEST_DELAY"]
MAX_RETRIES: int = SCREENING_CONFIG["MAX_RETRIES"]

# Nouvelles configurations pour gestion des dérivés et stablecoins
DERIVATIVE_MAPPING: dict[str, str] = SCREENING_CONFIG["DERIVATIVE_MAPPING"]
EXCLUDED_STABLECOINS: list[str] = SCREENING_CONFIG["EXCLUDED_STABLECOINS"]
NATIVE_COINS: list[str] = SCREENING_CONFIG["NATIVE_COINS"]


# ============================================================================
# FONCTIONS UTILITAIRES : CONSOLIDATION DES DÉRIVÉS ET FILTRAGE
# ============================================================================


def is_stablecoin(symbol: str, coingecko_id: str) -> bool:
    """
    Vérifie si un token est un stablecoin.

    Args:
        symbol: Symbole du token (ex: "USDT")
        coingecko_id: ID CoinGecko (ex: "tether")

    Returns:
        bool: True si stablecoin, False sinon
    """
    symbol_upper = symbol.upper()
    coingecko_lower = coingecko_id.lower()

    # Vérifier dans la liste d'exclusion (symboles et IDs)
    for excluded in EXCLUDED_STABLECOINS:
        excluded_upper = excluded.upper()
        if symbol_upper == excluded_upper or coingecko_lower == excluded.lower():
            return True

    return False


def resolve_derivative(coingecko_id: str) -> tuple[str, bool]:
    """
    Résout un dérivé vers son coin parent.

    Args:
        coingecko_id: ID CoinGecko du token (ex: "wrapped-bitcoin")

    Returns:
        tuple: (parent_id, is_derivative)
               - parent_id: ID du parent (ou le même si pas de dérivé)
               - is_derivative: True si c'est un dérivé
    """
    parent_id = DERIVATIVE_MAPPING.get(coingecko_id)

    if parent_id:
        return parent_id, True
    return coingecko_id, False


def compare_exchange_priority(exchange1: str, exchange2: str) -> int:
    """
    Compare la priorité de deux exchanges.

    Args:
        exchange1: Nom du premier exchange
        exchange2: Nom du second exchange

    Returns:
        int: -1 si exchange1 est meilleur (priorité supérieure)
              0 si égaux ou non trouvés
             +1 si exchange2 est meilleur
    """
    try:
        priority1 = (
            EXCHANGE_PRIORITY_ORDER.index(exchange1)
            if exchange1 in EXCHANGE_PRIORITY_ORDER
            else 9999
        )
        priority2 = (
            EXCHANGE_PRIORITY_ORDER.index(exchange2)
            if exchange2 in EXCHANGE_PRIORITY_ORDER
            else 9999
        )

        if priority1 < priority2:
            return -1  # exchange1 est meilleur (index plus petit = meilleure priorité)
        if priority1 > priority2:
            return 1  # exchange2 est meilleur
        return 0  # Égaux
    except Exception:
        return 0


# ============================================================================
# ÉTAPE 1 : RÉCUPÉRATION DU TOP X PAR MARKET CAP
# ============================================================================


def fetch_top_tokens_by_marketcap(top_limit: int = TOP_LIMIT) -> list[dict] | None:
    """
    Récupère les Top X tokens par MarketCap depuis CoinGecko via api_manager.

    Args:
        top_limit: Nombre de tokens à récupérer (max 250 par page)

    Returns:
        List[Dict]: Liste des tokens ou None en cas d'échec
    """
    logger.info(f"🔍 Récupération du Top {top_limit} tokens par MarketCap...")

    all_tokens: list[dict[str, Any]] = []
    page = 1
    per_page = 250  # Maximum autorisé par CoinGecko

    while len(all_tokens) < top_limit:
        url = f"{COINGECKO_BASE}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": page,
            "sparkline": False,
        }

        logger.info(
            f"   📄 Page {page} (tokens {len(all_tokens) + 1} à {min(len(all_tokens) + per_page, top_limit)})..."
        )

        # Utilisation d'api_manager avec rate limiting automatique
        data = api_manager.get_json(
            service="coingecko",
            url=url,
            params=params,
            timeout=30,
            max_retries=MAX_RETRIES,
        )

        if not data:
            logger.error(f"   ❌ Échec de récupération page {page}")
            break

        all_tokens.extend(data)

        # Vérifier si on a récupéré tous les tokens disponibles
        if len(data) < per_page:
            logger.info(
                f"   ✅ Tous les tokens disponibles récupérés ({len(all_tokens)})"
            )
            break

        # Vérifier si on a atteint la limite
        if len(all_tokens) >= top_limit:
            all_tokens = all_tokens[:top_limit]
            break

        page += 1
        # Pas besoin de sleep, api_manager gère le rate limiting

    logger.info(f"✅ {len(all_tokens)} tokens récupérés avec succès")
    return all_tokens


# ============================================================================
# ÉTAPE 1.5 : PRÉ-FILTRAGE ET CONSOLIDATION
# ============================================================================


def pre_filter_and_consolidate(top_coins: list[dict]) -> tuple[dict, dict]:
    """
    Pré-filtre les stablecoins et consolide les dérivés vers leur parent.

    Cette fonction optimise le screening en :
    1. Filtrant les stablecoins AVANT de faire les appels API individuels
    2. Résolvant les dérivés (WBTC → BTC) AVANT de faire les appels API
    3. Dédupliquant les parents (si BTC et WBTC sont dans le top, un seul appel pour BTC)

    Args:
        top_coins: Liste des tokens du Top X depuis CoinGecko

    Returns:
        tuple: (consolidated_tokens, pre_stats)
            - consolidated_tokens: {parent_id: {'data': coin_data, 'best_rank': int, ...}}
            - pre_stats: Statistiques du pré-filtrage
    """
    logger.info("🧹 Pré-filtrage et consolidation des tokens...")

    consolidated_tokens = {}
    pre_stats = {
        "stablecoins_excluded": 0,
        "derivatives_merged": 0,
        "total_input": len(top_coins),
        "total_output": 0,
    }

    for coin_data in top_coins:
        coingecko_id = coin_data["id"]
        symbol = coin_data["symbol"].upper()
        rank = coin_data.get("market_cap_rank", 9999)

        # ----------------------------------------------------------------
        # FILTRE 1 : EXCLUSION DES STABLECOINS
        # ----------------------------------------------------------------
        if is_stablecoin(symbol, coingecko_id):
            logger.debug(f"   🚫 Stablecoin exclu : {symbol} ({coingecko_id})")
            pre_stats["stablecoins_excluded"] += 1
            continue

        # ----------------------------------------------------------------
        # FILTRE 2 : RÉSOLUTION DES DÉRIVÉS VERS PARENT
        # ----------------------------------------------------------------
        parent_id, is_derivative = resolve_derivative(coingecko_id)

        if is_derivative:
            logger.debug(
                f"   🔗 Dérivé détecté : {symbol} ({coingecko_id}) → Parent ({parent_id})"
            )
            pre_stats["derivatives_merged"] += 1

        # ----------------------------------------------------------------
        # CONSOLIDATION : Grouper par parent_id
        # ----------------------------------------------------------------
        if parent_id not in consolidated_tokens:
            # Première occurrence de ce parent
            consolidated_tokens[parent_id] = {
                "data": coin_data,  # Données du token (sera écrasé si parent trouvé après)
                "ranks": [rank],
                "derivatives": [],
                "is_derivative": is_derivative,
                "original_id": coingecko_id,
                "symbol": symbol,
            }
        else:
            # Parent déjà vu : ajouter le rank
            consolidated_tokens[parent_id]["ranks"].append(rank)

            # Si c'est un dérivé, l'ajouter à la liste
            if is_derivative:
                consolidated_tokens[parent_id]["derivatives"].append(
                    {"id": coingecko_id, "symbol": symbol, "rank": rank}
                )

            # Si le parent original apparaît après son dérivé, mettre à jour les données
            if not is_derivative and consolidated_tokens[parent_id]["is_derivative"]:
                consolidated_tokens[parent_id]["data"] = coin_data
                consolidated_tokens[parent_id]["is_derivative"] = False
                consolidated_tokens[parent_id]["original_id"] = coingecko_id
                consolidated_tokens[parent_id]["symbol"] = symbol

        # Calculer le meilleur rank
        consolidated_tokens[parent_id]["best_rank"] = min(
            consolidated_tokens[parent_id]["ranks"]
        )

    pre_stats["total_output"] = len(consolidated_tokens)

    # ----------------------------------------------------------------
    # LOGGING DES STATISTIQUES
    # ----------------------------------------------------------------
    logger.info(f"   🚫 Stablecoins exclus : {pre_stats['stablecoins_excluded']}")
    logger.info(f"   🔗 Dérivés consolidés : {pre_stats['derivatives_merged']}")
    logger.info(
        f"   📉 Tokens à traiter : {pre_stats['total_output']} (au lieu de {pre_stats['total_input']})"
    )
    saved_calls = pre_stats["total_input"] - pre_stats["total_output"]
    logger.info(
        f"   💰 Appels API économisés : {saved_calls} ({saved_calls / pre_stats['total_input'] * 100:.1f}%)"
    )

    return consolidated_tokens, pre_stats


# ============================================================================
# ÉTAPE 2 : RÉCUPÉRATION DES DÉTAILS COMPLETS D'UN TOKEN
# ============================================================================


def get_token_full_data(coingecko_id: str) -> dict | None:
    """
    Récupère les données complètes d'un token (tickers, plateformes, liens sociaux) via api_manager.

    Args:
        coingecko_id: ID CoinGecko du token

    Returns:
        Dict: Données complètes du token ou None en cas d'échec
    """
    url = f"{COINGECKO_BASE}/coins/{coingecko_id}"
    params = {
        "localization": False,
        "tickers": True,
        "market_data": False,
        "community_data": False,
        "developer_data": False,
        "sparkline": False,
    }

    # Utilisation d'api_manager avec rate limiting automatique
    return api_manager.get_json(
        service="coingecko", url=url, params=params, timeout=30, max_retries=MAX_RETRIES
    )


# ============================================================================
# ÉTAPE 3 : LOGIQUE DE FILTRAGE DE LA MEILLEURE PAIRE
# ============================================================================


def find_best_pair_logic(
    full_data: dict, target_exchanges: list[str] = TARGET_EXCHANGES
) -> dict:
    """
    Sélectionne l'exchange selon l'ordre de priorité et calcule le volume total.

    Logique:
    1. Parcourt tous les tickers pour collecter les volumes par plateforme
    2. Calcule le volume total agrégé (toutes plateformes cibles)
    3. Choisit l'exchange selon l'ordre de priorité (MEXC > Bitget > Aster > Pionex...)

    Args:
        full_data: Données complètes du token depuis CoinGecko
        target_exchanges: Liste des plateformes cibles (ORDRE = PRIORITÉ)

    Returns:
        Dict: {
            'source': 'MEXC',
            'chain_id': 'CEX',
            'primary_pair_address': 'MEXC:BTC/USDT',
            'primary_volume_24h': 1000000,  # Volume sur MEXC
            'total_volume_24h': 5000000     # Volume total (toutes plateformes)
        }
    """
    platforms = full_data.get("platforms", {})
    found_exchanges: dict[
        str, dict[str, Any]
    ] = {}  # {exchange_name: {volume, pair, chain}}
    total_volume = 0.0

    # ========================================================================
    # ÉTAPE 1: Collecter tous les exchanges disponibles et leurs volumes
    # ========================================================================
    for ticker in full_data.get("tickers", []):
        exchange_name = ticker.get("market", {}).get("name", "")
        volume_24h = float(ticker.get("volume", 0))
        target_coin = ticker.get("target", "").upper()

        # FILTRE 1 : Doit être sur une plateforme cible
        if exchange_name not in target_exchanges:
            continue

        # FILTRE 2 : Doit avoir du volume
        if volume_24h == 0:
            continue

        # FILTRE 3 : Priorité aux paires stables (USDT, USDC, etc.)
        if target_coin not in TARGET_QUOTE_ASSETS:
            continue

        # Accumuler le volume total (toutes plateformes)
        total_volume += volume_24h

        # Garder le volume max pour cet exchange (si plusieurs paires)
        if (
            exchange_name not in found_exchanges
            or volume_24h > found_exchanges[exchange_name]["volume"]
        ):
            # Déterminer l'adresse de la paire et la chaîne
            pair_address = None
            chain_id = None
            market_identifier = ticker.get("market", {}).get("identifier", "")

            # Détection de la chaîne basée sur le nom de la plateforme
            if "pancakeswap" in exchange_name.lower():
                chain_id = "binance-smart-chain"
                pair_address = platforms.get("binance-smart-chain", market_identifier)
            elif "uniswap" in exchange_name.lower():
                chain_id = "ethereum"
                pair_address = platforms.get("ethereum", market_identifier)
            elif exchange_name.upper() in [
                "MEXC",
                "BITGET",
                "PIONEX",
                "ASTER",
                "HYPERLIQUID",
            ]:
                chain_id = "CEX"
                pair_address = f"{exchange_name}:{ticker.get('base', '')}/{target_coin}"
            else:
                # Autres DEX : essayer de trouver la chaîne via les plateformes
                for platform_chain, contract_addr in platforms.items():
                    if platform_chain not in IGNORE_CHAINS:
                        chain_id = platform_chain
                        pair_address = contract_addr
                        break

            found_exchanges[exchange_name] = {
                "volume": volume_24h,
                "pair_address": pair_address or "N/A",
                "chain_id": chain_id,
                "target_coin": target_coin,
            }

    # ========================================================================
    # ÉTAPE 2: Choisir selon l'ordre de priorité (EXCHANGE_PRIORITY_ORDER)
    # ========================================================================
    best_pair_info: dict[str, Any] = {
        "source": None,
        "chain_id": None,
        "primary_pair_address": None,
        "primary_volume_24h": 0.0,
        "total_volume_24h": total_volume,
    }

    # Parcourir l'ordre de priorité et prendre le premier trouvé
    for priority_exchange in EXCHANGE_PRIORITY_ORDER:
        if priority_exchange in found_exchanges:
            exchange_data = found_exchanges[priority_exchange]
            best_pair_info["source"] = priority_exchange
            best_pair_info["chain_id"] = exchange_data["chain_id"]
            best_pair_info["primary_pair_address"] = exchange_data["pair_address"]
            best_pair_info["primary_volume_24h"] = exchange_data["volume"]
            break

    return best_pair_info


# ============================================================================
# ÉTAPE 4 : EXTRACTION DES LIENS SOCIAUX
# ============================================================================


def extract_social_links(full_data: dict) -> dict[str, str | None]:
    """
    Extrait les liens sociaux (Twitter, Telegram) depuis les données CoinGecko.

    Args:
        full_data: Données complètes du token

    Returns:
        Dict: Liens sociaux (twitter_link, telegram_link)
    """
    links = full_data.get("links", {})

    twitter_username = links.get("twitter_screen_name")
    telegram_id = links.get("telegram_channel_identifier")

    twitter_link = (
        f"https://twitter.com/{twitter_username}" if twitter_username else None
    )
    telegram_link = f"https://t.me/{telegram_id}" if telegram_id else None

    return {
        "twitter_link": twitter_link,
        "telegram_link": telegram_link,
        "twitter_handle": twitter_username,
    }


# ============================================================================
# ORCHESTRATEUR PRINCIPAL
# ============================================================================


def fetch_top_x_and_screen(
    top_limit: int = TOP_LIMIT, target_exchanges: list[str] = TARGET_EXCHANGES
) -> dict[str, int]:
    """
    Récupère les tokens du Top X, trouve la paire la plus liquide et met à jour la DB.
    Implémente la logique de pré-nettoyage pour gérer les tokens sortis du Top X.

    Args:
        top_limit: Nombre de tokens à récupérer
        target_exchanges: Plateformes cibles

    Returns:
        Dict: Statistiques du screening (created, updated, skipped, errors, demoted)
    """
    # Réinitialiser le flag d'arrêt au début d'un nouveau screening
    global _shutdown_requested
    _shutdown_requested = False

    logger.info(f"🚀 Début du Screening du Top {top_limit}... (MAJ QUOTIDIENNE)")

    stats = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "demoted": 0,  # Tokens déclassés (OUT_OF_RANK)
        "derivatives_merged": 0,  # Dérivés consolidés vers parent
        "stablecoins_excluded": 0,  # Stablecoins exclus
        "incomplete": 0,  # Tokens marqués INCOMPLETE (infos manquantes)
    }

    # ----------------------------------------------------------------------
    # ÉTAPE 0 : PRÉ-NETTOYAGE - MARQUER TOUS LES TOKENS ACTIFS COMME "OUT_OF_RANK"
    # ----------------------------------------------------------------------
    logger.info(
        "   🧹 Pré-nettoyage : Déclassement des tokens actuellement en MONITORING..."
    )
    try:
        with db.atomic():
            demoted_count = (
                Token.update(rank_status="OUT_OF_RANK")
                .where(Token.rank_status == "MONITORING")
                .execute()
            )
            stats["demoted"] = demoted_count
            logger.info(f"   ➡️ {demoted_count} tokens marqués comme 'OUT_OF_RANK'")
    except Exception as e:
        logger.error(f"   ❌ Erreur lors du pré-nettoyage: {e}")

    # ----------------------------------------------------------------------
    # 1. RÉCUPÉRATION DU TOP X PAR MARKET CAP
    # ----------------------------------------------------------------------
    top_coins = fetch_top_tokens_by_marketcap(top_limit)
    if not top_coins:
        logger.error("❌ Échec de récupération des tokens")
        return stats

    # ----------------------------------------------------------------------
    # 1.5 PRÉ-FILTRAGE ET CONSOLIDATION (OPTIMISATION)
    # ----------------------------------------------------------------------
    consolidated_tokens, pre_stats = pre_filter_and_consolidate(top_coins)

    # Mettre à jour les stats avec le pré-filtrage
    stats["stablecoins_excluded"] = pre_stats["stablecoins_excluded"]
    stats["derivatives_merged"] = pre_stats["derivatives_merged"]

    # ----------------------------------------------------------------------
    # 2. PROCESSUS D'ONBOARDING POUR CHAQUE TOKEN CONSOLIDÉ
    # ----------------------------------------------------------------------
    total_tokens = len(consolidated_tokens)

    for idx, (parent_id, token_info) in enumerate(consolidated_tokens.items(), 1):
        # ----------------------------------------------------------------
        # VÉRIFICATION D'ARRÊT GRACIEUX (Ctrl+C)
        # ----------------------------------------------------------------
        if is_shutdown_requested():
            logger.warning(
                f"\n🛑 Arrêt demandé - Screening interrompu à {idx}/{total_tokens} tokens"
            )
            logger.info(
                f"📊 Statistiques partielles : {stats['created']} créés, {stats['updated']} mis à jour"
            )
            return stats

        # Récupérer les infos depuis token_info
        coin_data = token_info["data"]
        coingecko_id = parent_id  # Utiliser directement le parent_id
        symbol = token_info["symbol"]
        name = coin_data["name"]
        rank = token_info["best_rank"]  # Utiliser le meilleur rank

        # Log des dérivés consolidés
        derivatives_info = ""
        if token_info["derivatives"]:
            derivatives_symbols = [d["symbol"] for d in token_info["derivatives"]]
            derivatives_info = f" (+ dérivés: {', '.join(derivatives_symbols)})"

        logger.info(
            f"\n[{idx}/{total_tokens}] Processing {symbol} ({coingecko_id}) - Rank: {rank}{derivatives_info}"
        )

        # ----------------------------------------------------------------
        # Récupération des détails complets (tickers, plateformes, etc.)
        # Note : Les stablecoins et dérivés ont déjà été filtrés/consolidés
        # ----------------------------------------------------------------
        full_data = get_token_full_data(coingecko_id)
        if not full_data:
            logger.warning(f"   ⚠️ Échec de récupération des détails pour {symbol}")
            stats["errors"] += 1
            continue

        # Utiliser les données du parent (coingecko_id est déjà le parent_id après consolidation)
        symbol = full_data.get("symbol", symbol).upper()
        name = full_data.get("name", name)

        # Extraction des liens sociaux
        social_links = extract_social_links(full_data)

        # Récupération de l'adresse de contrat primaire depuis CoinGecko
        platforms = full_data.get("platforms", {})

        # Priorité : ethereum > binance-smart-chain > autres
        primary_contract = None
        if platforms.get("ethereum"):
            primary_contract = platforms.get("ethereum")
        elif platforms.get("binance-smart-chain"):
            primary_contract = platforms.get("binance-smart-chain")
        elif platforms:
            # Prendre la première adresse disponible
            primary_contract = next(iter(platforms.values())) if platforms else None

        # ================================================================
        # STRATÉGIE DE RECHERCHE DE PAIRE
        # ================================================================
        # MODIFICATION: On ne cherche plus la paire ici, DexScreener s'en chargera
        # On vérifie juste qu'on a un contrat

        if primary_contract:
            pass

        else:
            # CAS 2 : Pas de contrat sur CoinGecko → Marquer INCOMPLETE pour traitement ultérieur
            logger.warning(f"   ⚠️ Pas d'adresse de contrat sur CoinGecko pour {symbol}")
            logger.info(
                "   📋 Token marqué INCOMPLETE → Résolution différée via job scheduler"
            )

            # Créer/mettre à jour le token avec status INCOMPLETE
            try:
                # Chercher si le token existe déjà par coingecko_id
                existing_token = None
                with contextlib.suppress(Token.DoesNotExist):
                    existing_token = Token.get(Token.coingecko_id == coingecko_id)

                if existing_token:
                    # Mise à jour : marquer comme INCOMPLETE
                    existing_token.status = "INCOMPLETE"
                    existing_token.rank = (
                        rank
                        if rank < (existing_token.rank or 9999)
                        else existing_token.rank
                    )
                    existing_token.rank_status = "MONITORING"
                    existing_token.last_rank_update = datetime.now()

                    # Mettre à jour les infos disponibles
                    if social_links["twitter_link"]:
                        existing_token.twitter_link = social_links["twitter_link"]
                        existing_token.twitter_handle = social_links["twitter_handle"]
                    if social_links["telegram_link"]:
                        existing_token.telegram_link = social_links["telegram_link"]

                    existing_token.save()
                    logger.info(f"   🔄 Token existant marqué INCOMPLETE : {symbol}")
                else:
                    # Création : utiliser coingecko_id comme contrat temporaire
                    Token.create(
                        symbol=symbol,
                        name=name,
                        contract=f"INCOMPLETE:{coingecko_id}",  # Marqueur temporaire
                        cashtag=f"${symbol}",
                        coingecko_id=coingecko_id,
                        rank=rank,
                        twitter_link=social_links["twitter_link"],
                        twitter_handle=social_links["twitter_handle"],
                        telegram_link=social_links["telegram_link"],
                        status="INCOMPLETE",
                        rank_status="MONITORING",
                        last_rank_update=datetime.now(),
                    )
                    logger.info(
                        f"   ➕ Nouveau token créé avec status INCOMPLETE : {symbol}"
                    )

                stats["incomplete"] += 1
                continue  # Passer au token suivant rapidement

            except Exception as e:
                logger.error(
                    f"   ❌ Erreur lors du marquage INCOMPLETE pour {symbol}: {e}"
                )
                stats["errors"] += 1
                continue

        # 3. SAUVEGARDE EN BASE DE DONNÉES
        now = datetime.now()

        try:
            # Chercher par coingecko_id ou par contract
            existing_token = None
            with contextlib.suppress(Token.DoesNotExist):
                existing_token = Token.get(
                    (Token.coingecko_id == coingecko_id)
                    | (Token.contract == primary_contract.lower())
                )

            if existing_token:
                # Mise à jour du token existant
                existing_token.name = name
                existing_token.coingecko_id = coingecko_id

                # IMPORTANT : Garder le MEILLEUR rang (le plus petit)
                old_rank = existing_token.rank or 9999
                if rank < old_rank:
                    existing_token.rank = rank
                    logger.info(f"   📈 Meilleur rang trouvé : {old_rank} → {rank}")
                else:
                    logger.info(
                        f"   ✓ Rang conservé : {existing_token.rank} (actuel: {rank})"
                    )

                # ================================================================
                # LOGIQUE DE MISE À JOUR DE L'EXCHANGE
                # ================================================================
                # Mise à jour si:
                # 1. Nouveau rang meilleur (rank < old_rank)
                # 2. Même rang MAIS meilleur exchange disponible (priorité supérieure)
                # 3. Même rang ET même exchange → mettre à jour les volumes
                # ================================================================

                # MODIFICATION: On ne met plus à jour les infos de paire/volume depuis CoinGecko
                # On met seulement à jour le rang et les infos de base
                # Les infos de paire seront gérées par DexScreener/Resolve

                # Mettre à jour les contrats disponibles (merge)
                existing_token.contract_addresses = json.dumps(platforms)

                # NOUVEAU : Remettre en MONITORING et mettre à jour last_rank_update
                existing_token.rank_status = "MONITORING"
                existing_token.last_rank_update = now

                # Mettre à jour les liens sociaux si disponibles
                if social_links["twitter_link"]:
                    existing_token.twitter_link = social_links["twitter_link"]
                    existing_token.twitter_handle = social_links["twitter_handle"]
                if social_links["telegram_link"]:
                    existing_token.telegram_link = social_links["telegram_link"]

                existing_token.save()
                stats["updated"] += 1
                logger.info(
                    f"   🔄 Mis à jour : {symbol} (Rank: {existing_token.rank})"
                )
            else:
                # Création d'un nouveau token
                Token.create(
                    symbol=symbol,
                    name=name,
                    contract=primary_contract.lower(),
                    cashtag=f"${symbol}",
                    coingecko_id=coingecko_id,
                    rank=rank,
                    # MODIFICATION: Pas d'infos de paire/volume initiales
                    contract_addresses=json.dumps(platforms),
                    twitter_link=social_links["twitter_link"],
                    twitter_handle=social_links["twitter_handle"],
                    telegram_link=social_links["telegram_link"],
                    status="PENDING",  # Mode surveillance: PENDING
                    rank_status="MONITORING",  # Statut de classement: MONITORING
                    last_rank_update=now,
                )
                stats["created"] += 1
                logger.info(f"   ✅ CRÉÉ : {symbol} (Rank: {rank})")

        except Exception as e:
            logger.error(f"   ❌ Erreur DB pour {symbol}: {e}")
            stats["errors"] += 1

        # Pas besoin de sleep, api_manager gère le rate limiting

    # ----------------------------------------------------------------------
    # 3. RÉSUMÉ DU SCREENING
    # ----------------------------------------------------------------------
    logger.info(f"\n{'=' * 80}")
    logger.info("✅ SCREENING TERMINÉ")
    logger.info(f"{'=' * 80}")
    logger.info(f"📊 Tokens créés       : {stats['created']}")
    logger.info(f"🔄 Tokens mis à jour  : {stats['updated']}")
    logger.info(f"📉 Tokens déclassés   : {stats['demoted']}")
    logger.info(f"🔗 Dérivés consolidés : {stats['derivatives_merged']}")
    logger.info(f"🚫 Stablecoins exclus : {stats['stablecoins_excluded']}")
    logger.info(f"📋 Tokens INCOMPLETE  : {stats['incomplete']}")
    logger.info(f"⏭️  Tokens ignorés     : {stats['skipped']}")
    logger.info(f"❌ Erreurs            : {stats['errors']}")
    logger.info(f"{'=' * 80}")
    logger.info(
        f"💡 Les tokens sortis du Top {top_limit} ont été conservés avec le statut OUT_OF_RANK"
    )
    if stats["incomplete"] > 0:
        logger.info(
            f"💡 {stats['incomplete']} token(s) INCOMPLETE seront résolus par le job scheduler"
        )
    logger.info(f"{'=' * 80}\n")

    return stats


# ============================================================================
# FONCTIONS DE RECHERCHE DANS LA BASE DE DONNÉES
# ============================================================================


def find_token_by_symbol_or_address(identifier: str) -> Token | None:
    """
    Recherche un token dans la DB par symbol/cashtag ou adresse de contrat.

    Args:
        identifier: Symbol (ex: "WAVES", "$WAVES") ou adresse de contrat (ex: "0x123...")

    Returns:
        Token trouvé ou None
    """
    # Nettoyer l'identifier
    identifier = identifier.strip()

    # Si ça commence par $, c'est un cashtag
    if identifier.startswith("$"):
        symbol = identifier[1:].upper()
        try:
            return Token.get(Token.symbol == symbol)  # type: ignore[no-any-return]
        except Token.DoesNotExist:
            return None

    # Si ça ressemble à une adresse (commence par 0x et longueur 42), chercher par contract
    if identifier.startswith("0x") and len(identifier) == 42:
        try:
            return Token.get(Token.contract == identifier.lower())  # type: ignore[no-any-return]
        except Token.DoesNotExist:
            return None

    # Sinon, considérer comme un symbol
    symbol = identifier.upper()
    try:
        return Token.get(Token.symbol == symbol)  # type: ignore[no-any-return]
    except Token.DoesNotExist:
        return None


def get_token_info_for_monitoring(token: Token) -> dict[str, Any] | None:
    """
    Récupère les informations nécessaires pour lancer le monitoring d'un token.

    Args:
        token: Token de la DB

    Returns:
        Dict avec les infos ou None si données insuffisantes
    """
    # Vérifier que le token a les infos nécessaires
    if not token.primary_pair_address or token.primary_pair_address == "N/A":
        logger.warning(f"⚠️ Token {token.cashtag} n'a pas de paire primaire définie")
        return None

    # Déterminer chain_id et pair_address à utiliser
    chain_id = token.primary_chain_id or token.chain_id
    pair_address = token.primary_pair_address or token.pair_address

    if not chain_id or not pair_address:
        logger.warning(f"⚠️ Token {token.cashtag} manque chain_id ou pair_address")
        return None

    return {
        "symbol": token.symbol,
        "cashtag": token.cashtag,
        "contract": token.contract,
        "chain_id": chain_id,
        "pair_address": pair_address,
        "primary_price_source": token.primary_price_source,
        "twitter_link": token.twitter_link,
        "telegram_link": token.telegram_link,
        "rank": token.rank,
        "rank_status": token.rank_status,
    }


# ============================================================================
# FONCTION DE FILTRAGE DE LA BASE DE DONNÉES
# ============================================================================


def filter_tokens_by_criteria(
    min_rank: int | None = None,
    max_rank: int | None = None,
    exchanges: list[str] | None = None,
    chains: list[str] | None = None,
    status: str | None = None,
    rank_status: str | None = None,
) -> list[Token]:
    """
    Filtre les tokens en base de données selon des critères.

    Args:
        min_rank: Rang minimum (ex: 1)
        max_rank: Rang maximum (ex: 500)
        exchanges: Liste des plateformes (ex: ['MEXC', 'PancakeSwap'])
        chains: Liste des chaînes (ex: ['ethereum', 'binance-smart-chain'])
        status: Statut de surveillance (ex: 'REGULAR', 'PENDING')
        rank_status: Statut de classement (ex: 'MONITORING', 'OUT_OF_RANK', 'IGNORED')

    Returns:
        List[Token]: Liste des tokens filtrés
    """
    query = Token.select()

    # Filtre par rang
    if min_rank is not None:
        query = query.where(Token.rank >= min_rank)
    if max_rank is not None:
        query = query.where(Token.rank <= max_rank)

    # Filtre par plateforme
    if exchanges:
        query = query.where(Token.primary_price_source.in_(exchanges))

    # Filtre par chaîne
    if chains:
        query = query.where(Token.primary_chain_id.in_(chains))

    # Filtre par statut de surveillance
    if status:
        query = query.where(Token.status == status)

    # Filtre par statut de classement
    if rank_status:
        query = query.where(Token.rank_status == rank_status)

    return list(query.order_by(Token.rank))


# ============================================================================
# TEST ET DEBUGGING
# ============================================================================

if __name__ == "__main__":
    print("🧪 Test du Token Screener\n" + "=" * 80)

    # Configuration du logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Test avec le Top 10 pour un test rapide
    print("\n[TEST] Lancement du screening du Top 10 tokens...")
    stats = fetch_top_x_and_screen(top_limit=10)

    print("\n[TEST] Statistiques finales:")
    print(f"   Créés   : {stats['created']}")
    print(f"   Mis à jour : {stats['updated']}")
    print(f"   Ignorés : {stats['skipped']}")
    print(f"   Erreurs : {stats['errors']}")

    print("\n" + "=" * 80)
