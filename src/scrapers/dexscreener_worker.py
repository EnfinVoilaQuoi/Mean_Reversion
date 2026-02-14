"""
DexScreener Worker - Recherche de tokens et paires via l'API DexScreener
Utilisé comme fallback quand CoinGecko ne retourne pas d'adresse de contrat

NOUVELLE APPROCHE (2025):
- Utilise /token-pairs/v1/{chainId}/{tokenAddress} pour récupérer TOUTES les paires d'un token
- Identifie la paire la plus liquide (primary_pair) pour chaque blockchain
- Somme les volumes 24h de toutes les paires pour un volume global
- Applique ensuite la priorité d'exchange APRÈS avoir identifié toutes les paires disponibles
"""

import logging
from typing import Any, cast

from ..utils.api_manager import api_manager
from ..utils.api_schemas import DexScreenerSearchResponse
from ..utils.error_handlers import retry_db_operation

logger = logging.getLogger(__name__)

# Configuration
DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex"

# Exchange priority configuration (extracted from archived SCREENING_CONFIG)
EXCHANGE_PRIORITY_ORDER = [
    "MEXC",
    "Bitget",
    "Aster",
    "Pionex",
    "PancakeSwap",
    "Uniswap",
    "HyperLiquid",
]
TARGET_EXCHANGES = EXCHANGE_PRIORITY_ORDER  # Alias for compatibility
TARGET_QUOTE_ASSETS = ["USDT", "USDC", "WETH", "WBNB", "ETH", "BNB"]
MIN_LIQUIDITY_USD = 10000  # Minimum 10k$ de liquidité pour être considéré


# ============================================================================
# ÉTAPE 1 : RECHERCHE INITIALE DU TOKEN (Symbol → Contract Address + Chains)
# ============================================================================


def search_token_on_dexscreener(symbol: str) -> dict | None:
    """
    Recherche un token sur DexScreener par son symbol.
    ÉTAPE 1 : Récupère un exemple de paire pour extraire le contract address.

    Args:
        symbol: Symbol du token (ex: "BTC", "ETH")

    Returns:
        Dict avec une paire d'exemple (pour extraire contract et chainId) ou None si échec
    """
    url = f"{DEXSCREENER_BASE}/search"
    params = {"q": symbol}

    logger.info(f"   🔍 Recherche initiale DexScreener pour '{symbol}'...")

    # Utilisation de la validation Pydantic
    validated_data = api_manager.get_validated_json(
        service="dexscreener",
        url=url,
        params=params,
        schema=DexScreenerSearchResponse,
        timeout=15,
        max_retries=2,
    )

    if not validated_data or not validated_data.pairs:
        logger.warning(f"   ⚠️ Aucune paire trouvée pour {symbol}")
        return None

    logger.info(f"   ✅ Recherche trouvée : {len(validated_data.pairs)} paire(s)")

    # Convertir en dict pour compatibilité avec le code existant
    return {"pairs": [pair.model_dump() for pair in validated_data.pairs]}


def get_all_pairs_for_token(
    contract_address: str,
    chain_id: str,
) -> list[dict] | None:
    """
    ÉTAPE 2 : Récupère TOUTES les paires d'un token sur une blockchain spécifique
    via l'endpoint /token-pairs/v1/{chainId}/{tokenAddress}

    Args:
        contract_address: Adresse du contrat du token
        chain_id: ID de la blockchain (ex: "ethereum", "solana")

    Returns:
        Liste des paires avec tous leurs détails (volume, liquidité, etc.) ou None si échec
    """
    url = f"{DEXSCREENER_BASE}/token-pairs/v1/{chain_id}/{contract_address}"

    logger.debug(f"   📡 Appel API DexScreener : {url}")

    try:
        # Récupérer la réponse avec validation Pydantic
        validated_data = api_manager.get_validated_json(
            service="dexscreener",
            url=url,
            params={},
            schema=DexScreenerSearchResponse,
            timeout=15,
            max_retries=2,
        )

        if not validated_data or not validated_data.pairs:
            logger.debug(f"   ⚠️ Pas de réponse pour {contract_address} sur {chain_id}")
            return None

        pairs_data = [pair.model_dump() for pair in validated_data.pairs]

        if not pairs_data:
            logger.debug(
                f"   ℹ️  Aucune paire trouvée pour {contract_address} sur {chain_id}"
            )
            return None

        logger.info(
            f"   ✅ {len(pairs_data)} paire(s) récupérée(s) pour {contract_address} sur {chain_id}"
        )
        return pairs_data

    except Exception as e:
        logger.warning(f"   ❌ Erreur lors de la récupération des paires : {e}")
        return None


def map_dexid_to_exchange(dex_id: str) -> str | None:
    """
    Mappe les dexId de DexScreener vers nos noms d'exchanges standardisés.

    Args:
        dex_id: ID du DEX depuis DexScreener (ex: "uniswap", "pancakeswap")

    Returns:
        Nom standardisé de l'exchange ou None
    """
    # Mapping des dexId DexScreener vers nos noms d'exchanges
    mapping = {
        # DEX
        "uniswap": "Uniswap",
        "pancakeswap": "PancakeSwap",
        "sushiswap": "SushiSwap",
        "raydium": "Raydium",
        "orca": "Orca",
        "aerodrome": "Aerodrome",
        "velodrome": "Velodrome",
        # CEX (si disponibles sur DexScreener - rare)
        "mexc": "MEXC",
        "bitget": "Bitget",
        "pionex": "Pionex",
        # Autres
        "hyperliquid": "HyperLiquid",
    }

    return mapping.get(dex_id.lower())


# ============================================================================
# ÉTAPE 3 : SÉLECTION DE LA PAIRE LA PLUS LIQUIDE PAR BLOCKCHAIN
# ============================================================================


def select_best_pair_by_liquidity(pairs: list[dict]) -> dict | None:
    """
    ÉTAPE 3 : Sélectionne la paire la plus liquide (highest liquidity.usd) parmi toutes les paires.

    Args:
        pairs: Liste des paires (résultat de get_all_pairs_for_token)

    Returns:
        La paire avec la liquidité la plus élevée, ou None
    """
    if not pairs:
        return None

    # Filtrer les paires valides
    valid_pairs: list[dict[str, Any]] = []
    for pair in pairs:
        liquidity = pair.get("liquidity", {})
        liquidity_usd = liquidity.get("usd", 0) if isinstance(liquidity, dict) else 0

        # Vérifier les critères minimums
        if liquidity_usd < MIN_LIQUIDITY_USD:
            continue

        quote_symbol = pair.get("quoteToken", {}).get("symbol", "").upper()
        if quote_symbol not in TARGET_QUOTE_ASSETS:
            continue

        volume_24h = pair.get("volume", {}).get("h24", 0) if pair.get("volume") else 0
        if volume_24h == 0:
            continue

        valid_pairs.append(
            {"pair": pair, "liquidity_usd": liquidity_usd, "volume_24h": volume_24h}
        )

    if not valid_pairs:
        logger.debug("   ⚠️ Aucune paire valide trouvée")
        return None

    # Sélectionner la paire avec la liquidité maximale
    best = max(valid_pairs, key=lambda x: float(cast(dict, x).get("liquidity_usd", 0)))

    logger.info(
        f"   ✅ Paire primaire sélectionnée : {best['pair'].get('pairAddress')} "
        f"(Liq: ${best['liquidity_usd']:,.0f})"
    )

    return cast(dict[str, Any], best["pair"])


def calculate_total_volume(pairs: list[dict]) -> float:
    """
    ÉTAPE 4 : Somme tous les volumes 24h de toutes les paires.

    Args:
        pairs: Liste des paires

    Returns:
        Volume total cumulé (en USD)
    """
    total_volume = 0.0

    for pair in pairs:
        volume = pair.get("volume", {})
        volume_24h = volume.get("h24", 0) if isinstance(volume, dict) else 0
        total_volume += volume_24h

    logger.info(f"   📊 Volume total agrégé : ${total_volume:,.0f}")
    return total_volume


# ============================================================================
# ÉTAPE 5 : APPLICATION DE LA PRIORITÉ D'EXCHANGE
# ============================================================================


def find_best_exchange_for_pairs(
    pairs: list[dict], symbol: str, target_exchanges: list[str] = TARGET_EXCHANGES
) -> dict | None:
    """
    ÉTAPE 5 : Sélectionne la paire avec l'exchange ayant la plus haute priorité.

    Nouvelle logique (2025):
    1. Groupe les paires par exchange
    2. Garde la paire la plus liquide pour CHAQUE exchange
    3. Sélectionne l'exchange avec la plus haute priorité (EXCHANGE_PRIORITY_ORDER)
    4. Retourne les infos complètes de ce token/paire

    Args:
        pairs: Liste des paires (de get_all_pairs_for_token)
        symbol: Symbol du token
        target_exchanges: Liste des exchanges cibles

    Returns:
        Dict avec contract, source, chain_id, primary_pair_address, volumes
    """
    if not pairs:
        logger.warning(f"   ⚠️ Aucune paire pour {symbol}")
        return None

    # Grouper les paires par exchange
    exchanges_pairs: dict[
        str, dict[str, Any]
    ] = {}  # {exchange_name: {volume_24h, pair_address, liquidity_usd, chain_id, contract}}

    for pair in pairs:
        # Vérifier le symbole du token de base
        base_symbol = pair.get("baseToken", {}).get("symbol", "").upper()
        if base_symbol != symbol.upper():
            continue

        dex_id = pair.get("dexId", "")
        exchange_name = map_dexid_to_exchange(dex_id)

        # Filtre : exchange doit être dans la liste cible
        if not exchange_name or exchange_name not in target_exchanges:
            continue

        # Filtrer les critères de qualité
        liquidity_usd = pair.get("liquidity", {}).get("usd", 0)
        if liquidity_usd < MIN_LIQUIDITY_USD:
            continue

        quote_symbol = pair.get("quoteToken", {}).get("symbol", "").upper()
        if quote_symbol not in TARGET_QUOTE_ASSETS:
            continue

        volume_24h = pair.get("volume", {}).get("h24", 0)
        if volume_24h == 0:
            continue

        # Garder la paire avec le meilleur volume pour cet exchange
        if (
            exchange_name not in exchanges_pairs
            or volume_24h > exchanges_pairs[exchange_name]["volume_24h"]
        ):
            exchanges_pairs[exchange_name] = {
                "volume_24h": volume_24h,
                "pair_address": pair.get("pairAddress"),
                "liquidity_usd": liquidity_usd,
                "chain_id": pair.get("chainId"),
                "contract": pair.get("baseToken", {}).get("address"),
            }

    if not exchanges_pairs:
        logger.warning(f"   ⚠️ Aucun exchange valide trouvé pour {symbol}")
        return None

    # Sélectionner selon la priorité
    selected_exchange = None
    for priority_exchange in EXCHANGE_PRIORITY_ORDER:
        if priority_exchange in exchanges_pairs:
            selected_exchange = priority_exchange
            break

    if not selected_exchange:
        logger.warning(f"   ⚠️ Aucun exchange prioritaire trouvé pour {symbol}")
        return None

    # Calculer le volume total
    total_volume = calculate_total_volume(pairs)

    exchange_data = exchanges_pairs[selected_exchange]
    result = {
        "contract": exchange_data["contract"],
        "source": selected_exchange,
        "chain_id": exchange_data["chain_id"],
        "primary_pair_address": exchange_data["pair_address"],
        "primary_volume_24h": exchange_data["volume_24h"],
        "total_volume_24h": total_volume,
        "liquidity_usd": exchange_data["liquidity_usd"],
    }

    logger.info(
        f"   ✅ Exchange sélectionné : {selected_exchange} "
        f"(Vol: ${exchange_data['volume_24h']:,.0f}, Liq: ${exchange_data['liquidity_usd']:,.0f})"
    )

    return result


def find_best_pair_from_dexscreener(
    dexscreener_data: dict, symbol: str, target_exchanges: list[str] = TARGET_EXCHANGES
) -> dict | None:
    """
    LEGACY FUNCTION (pour compatibilité)
    Sélectionne la meilleure paire depuis les résultats DexScreener de /search.

    Logique:
    1. Filtre les paires selon TARGET_EXCHANGES et critères de qualité
    2. Calcule le volume total agrégé
    3. Sélectionne selon EXCHANGE_PRIORITY_ORDER

    Args:
        dexscreener_data: Données retournées par DexScreener (/search)
        symbol: Symbol du token recherché
        target_exchanges: Liste des exchanges cibles

    Returns:
        Dict avec contract, source, chain_id, primary_pair_address, volumes
    """
    pairs = dexscreener_data.get("pairs", [])
    return find_best_exchange_for_pairs(pairs, symbol, target_exchanges)


def get_token_info_from_dexscreener_v2(
    contract_address: str, chain_id: str, symbol: str
) -> dict | None:
    """
    NOUVELLE APPROCHE (2025) : Récupère les infos d'un token en utilisant l'endpoint /token-pairs/v1.

    Flux complet :
    1. Appel à /token-pairs/v1/{chainId}/{tokenAddress} pour récupérer TOUTES les paires
    2. Sélection de la paire la plus liquide (primary_pair)
    3. Calcul du volume total (somme de toutes les paires)
    4. Application de la priorité d'exchange

    Args:
        contract_address: Adresse du contrat du token
        chain_id: ID de la blockchain (ex: "ethereum", "solana")
        symbol: Symbol du token (ex: "BTC", "ETH")

    Returns:
        Dict avec contract, source, chain_id, primary_pair_address, volumes
        ou None si aucune paire valide trouvée
    """
    logger.info(f"\n   🔄 Recherche complète de {symbol} ({chain_id})...")

    # 1. Récupérer TOUTES les paires du token sur cette blockchain
    all_pairs = get_all_pairs_for_token(contract_address, chain_id)
    if not all_pairs:
        logger.warning(f"   ❌ Aucune paire trouvée pour {symbol}")
        return None

    logger.info(f"   📊 {len(all_pairs)} paire(s) trouvée(s)")

    # 2. Identifier la paire primaire (la plus liquide)
    primary_pair = select_best_pair_by_liquidity(all_pairs)
    if not primary_pair:
        logger.warning(f"   ⚠️ Aucune paire de qualité trouvée pour {symbol}")
        return None

    # 3. Appliquer la priorité d'exchange et calculer le volume total
    result = find_best_exchange_for_pairs(all_pairs, symbol)

    if result:
        logger.info(f"   ✅ Infos complètes récupérées pour {symbol}")

    return result


def get_token_info_from_dexscreener(symbol: str) -> dict[str, Any] | None:
    """
    Fonction principale (LEGACY) : recherche un token sur DexScreener par symbole et retourne ses infos.

    Cette fonction est appelée quand CoinGecko ne retourne pas d'adresse de contrat.
    Utilise l'ancien endpoint /dex/search (keep pour compatibilité).

    Args:
        symbol: Symbol du token (ex: "BTC", "ETH")

    Returns:
        Dict avec contract, source, chain_id, pair_address, volumes
        ou None si aucune paire de qualité trouvée
    """
    # 1. Rechercher le token sur DexScreener
    dexscreener_data = search_token_on_dexscreener(symbol)
    if not dexscreener_data:
        return None

    # 2. Sélectionner la meilleure paire
    return find_best_pair_from_dexscreener(dexscreener_data, symbol)



# ============================================================================
# RÉSOLUTION BATCH DES TOKENS INCOMPLETS
# ============================================================================


def resolve_single_token(token_symbol: str) -> dict | None:
    """
    Résout un token unique en recherchant ses infos sur DexScreener.
    Utilisé pour cibler un token spécifique (ex: via Discord).

    AMÉLIORATION: Gère les tokens multi-chain correctement
    - Recherche TOUTES les chains où le token existe
    - Query chaque chain via /token-pairs/v1
    - Sélectionne la chain avec la meilleure liquidité

    Args:
        token_symbol: Symbol du token à résoudre (ex: "TURBO")

    Returns:
        Dict avec les résultats ou None si échec
    """
    import json

    from ..database.models import Token

    logger.info(f"🔍 Résolution ciblée du token {token_symbol}...")

    # 1. Chercher le token en base de données
    try:
        token = Token.get(Token.symbol == token_symbol.upper())
    except Token.DoesNotExist:
        logger.warning(f"❌ Token {token_symbol} non trouvé en base de données")
        return {
            "success": False,
            "error": "Token not found in database",
            "token": token_symbol,
        }

    logger.info(f"📌 Token trouvé: {token.symbol} (status: {token.status})")

    # 2. Rechercher le token sur DexScreener (obtenir TOUTES les chains)
    search_result = search_token_on_dexscreener(token.symbol)

    if not search_result or not search_result.get("pairs"):
        logger.warning(f"❌ Aucune paire trouvée pour {token.symbol} sur DexScreener")
        return {
            "success": False,
            "error": "No pairs found on DexScreener",
            "token": token.symbol,
            "status": token.status,
        }

    # 3. Grouper les paires par chain et sélectionner la meilleure paire par chain
    chains_data: dict[
        str, dict[str, Any]
    ] = {}  # {chain_id: {pairs: [...], best_pair: {...}}}

    for pair in search_result["pairs"]:
        chain_id = pair.get("chainId", "")

        if not chain_id:
            continue

        if chain_id not in chains_data:
            chains_data[chain_id] = {"pairs": []}

        chains_data[chain_id]["pairs"].append(pair)

    if not chains_data:
        logger.warning(f"❌ Aucune chain valide trouvée pour {token.symbol}")
        return {
            "success": False,
            "error": "No valid chains found",
            "token": token.symbol,
        }

    logger.info(
        f"   📊 {len(chains_data)} chain(s) trouvée(s): {', '.join(chains_data.keys())}"
    )

    # 4. Pour CHAQUE chain, traiter les paires trouvées par /dex/search
    best_chain_result = None
    best_chain_liquidity = 0
    all_chains_results = []

    for chain_id, chain_info in chains_data.items():
        logger.info(f"   🔄 Analyse des paires pour {chain_id}...")

        pairs = chain_info["pairs"]

        # Appliquer priorité d'exchange et calculer volumes pour cette chain
        chain_result = find_best_exchange_for_pairs(pairs, token.symbol)

        if chain_result:
            chain_result["_chain_id"] = chain_id
            chain_result["_all_pairs_count"] = len(pairs)
            all_chains_results.append(chain_result)

            liquidity = chain_result.get("liquidity_usd", 0)

            logger.info(
                f"   ✅ {chain_id}: {chain_result['source']} "
                f"(Liq: ${liquidity:,.0f}, Vol: ${chain_result['primary_volume_24h']:,.0f})"
            )

            # Garder le meilleur (par liquidité)
            if liquidity > best_chain_liquidity:
                best_chain_liquidity = liquidity
                best_chain_result = chain_result

    if not best_chain_result:
        logger.warning("❌ Aucun résultat valide après analyse des paires")
        return {
            "success": False,
            "error": "No valid results from pair analysis",
            "token": token.symbol,
            "chains_tested": len(chains_data),
        }

    logger.info(
        f"   🎯 Chain sélectionnée: {best_chain_result['chain_id']} "
        f"(meilleure liquidité: ${best_chain_liquidity:,.0f})"
    )

    # 5. Mettre à jour le token avec retry
    def update_and_save():
        token.contract = best_chain_result["contract"]
        token.primary_price_source = best_chain_result["source"]
        token.primary_chain_id = best_chain_result["chain_id"]
        token.primary_pair_address = best_chain_result["primary_pair_address"]
        token.primary_volume_24h = best_chain_result["primary_volume_24h"]
        token.total_volume_24h = best_chain_result["total_volume_24h"]

        # Créer contract_addresses dict avec TOUTES les chains trouvées
        platforms = {}
        for chain_id, chain_info in chains_data.items():
            # Extraire le contract de la première paire de cette chain
            if chain_info["pairs"]:
                contract_addr = (
                    chain_info["pairs"][0].get("baseToken", {}).get("address", "")
                )
                if contract_addr:
                    platforms[chain_id] = contract_addr
        token.contract_addresses = json.dumps(platforms)

        # Changer status à LISTED si INCOMPLETE
        old_status = token.status
        if token.status == "INCOMPLETE":
            token.status = "LISTED"

        token.save()
        return old_status

    old_status = retry_db_operation(
        update_and_save,
        max_retries=3,
        initial_delay=0.5,
        context=f"Résolution de {token.symbol}",
    )

    if old_status is None:
        return {
            "success": False,
            "error": "Database locked (failed after retries)",
            "token": token.symbol,
        }

    # Succès
    result = {
        "success": True,
        "token": token.symbol,
        "old_status": old_status,
        "new_status": token.status,
        "source": best_chain_result["source"],
        "chain_id": best_chain_result["chain_id"],
        "contract": best_chain_result["contract"],
        "pair_address": best_chain_result["primary_pair_address"],
        "primary_volume_24h": best_chain_result["primary_volume_24h"],
        "total_volume_24h": best_chain_result["total_volume_24h"],
        "liquidity_usd": best_chain_result.get("liquidity_usd", 0),
        "chains_found": len(all_chains_results),
        "chains_tested": len(chains_data),
    }

    logger.info(
        f"✅ Résolu : {token.symbol} → {best_chain_result['source']} "
        f"({best_chain_result['chain_id']}) - Liq: ${best_chain_liquidity:,.0f}, "
        f"Vol: ${best_chain_result['primary_volume_24h']:,.0f}"
    )

    return result


def resolve_incomplete_tokens() -> dict[str, int]:
    """
    Job schedulé : résout tous les tokens marqués INCOMPLETE en recherchant
    leurs infos sur DexScreener.

    Cette fonction est appelée après le screening quotidien pour compléter
    les tokens sans adresse de contrat.

    Returns:
        Dict: Statistiques de résolution (resolved, failed, skipped)
    """
    import json

    from ..database.models import Token

    logger.info("🔍 Démarrage de la résolution des tokens INCOMPLETE...")

    # Statistiques
    stats = {"resolved": 0, "failed": 0, "skipped": 0}

    # 1. Récupérer tous les tokens INCOMPLETE
    incomplete_tokens = list(Token.select().where(Token.status == "INCOMPLETE"))

    if not incomplete_tokens:
        logger.info("   ℹ️  Aucun token INCOMPLETE à résoudre")
        return stats

    logger.info(f"   📋 {len(incomplete_tokens)} token(s) INCOMPLETE à traiter")

    # 2. Traiter chaque token
    for idx, token in enumerate(incomplete_tokens, 1):
        logger.info(
            f"\n[{idx}/{len(incomplete_tokens)}] Résolution de {token.symbol}..."
        )

        # Rechercher sur DexScreener
        dexscreener_info = get_token_info_from_dexscreener(token.symbol)

        if not dexscreener_info:
            logger.warning(f"❌ Aucune paire trouvée pour {token.symbol}")
            stats["failed"] += 1
            continue

        # Mettre à jour le token avec retry
        def update_and_save(_token=token, _info=dexscreener_info):
            _token.contract = _info["contract"]
            _token.primary_price_source = _info["source"]
            _token.primary_chain_id = _info["chain_id"]
            _token.primary_pair_address = _info["primary_pair_address"]
            _token.primary_volume_24h = _info["primary_volume_24h"]
            _token.total_volume_24h = _info["total_volume_24h"]

            # Créer contract_addresses dict
            platforms = {_info["chain_id"]: _info["contract"]}
            _token.contract_addresses = json.dumps(platforms)

            # Changer status à LISTED
            _token.status = "LISTED"
            _token.save()

        success = retry_db_operation(
            update_and_save,
            max_retries=3,
            initial_delay=0.5,
            context=f"Résolution de {token.symbol}",
        )

        if success is not None:
            logger.info(
                f"✅ Résolu : {token.symbol} → {dexscreener_info['source']} "
                f"(Vol: ${dexscreener_info['primary_volume_24h']:,.0f}) - Status: LISTED"
            )
            stats["resolved"] += 1
        else:
            stats["failed"] += 1

    # 3. Résumé
    logger.info(f"\n{'=' * 80}")
    logger.info("✅ RÉSOLUTION DES TOKENS INCOMPLETE TERMINÉE")
    logger.info(f"{'=' * 80}")
    logger.info(f"✅ Tokens résolus : {stats['resolved']}")
    logger.info(f"❌ Échecs         : {stats['failed']}")
    logger.info(f"⏭️  Ignorés        : {stats['skipped']}")
    logger.info(f"{'=' * 80}")

    if stats["resolved"] > 0:
        logger.info(
            f"💡 {stats['resolved']} token(s) passés à LISTED → Prêts pour activation manuelle"
        )

    return stats


# ============================================================================
# TEST ET DEBUGGING
# ============================================================================

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    print("\n" + "=" * 100)
    print("🧪 TEST 1: Ancienne approche (search)")
    print("=" * 100)

    # Test 1 : Ancienne approche avec /dex/search
    result_v1 = get_token_info_from_dexscreener("BTC")

    if result_v1:
        print("\n✅ Résultat (v1 - search):")
        print(f"   Contract     : {result_v1['contract']}")
        print(f"   Exchange     : {result_v1['source']}")
        print(f"   Chain        : {result_v1['chain_id']}")
        print(f"   Pair Address : {result_v1['primary_pair_address']}")
        print(f"   Volume 24h   : ${result_v1['primary_volume_24h']:,.0f}")
        print(f"   Total Vol    : ${result_v1['total_volume_24h']:,.0f}")
        print(f"   Liquidité    : ${result_v1['liquidity_usd']:,.0f}")
    else:
        print("\n❌ Aucune paire trouvée (v1)")

    print("\n" + "=" * 100)
    print("🧪 TEST 2: Nouvelle approche (token-pairs/v1)")
    print("=" * 100)

    # Test 2 : Nouvelle approche avec /token-pairs/v1
    # D'abord, récupérer le contract et chain d'une recherche
    if result_v1:
        result_v2 = get_token_info_from_dexscreener_v2(
            contract_address=result_v1["contract"],
            chain_id=result_v1["chain_id"],
            symbol="BTC",
        )

        if result_v2:
            print("\n✅ Résultat (v2 - token-pairs/v1):")
            print(f"   Contract     : {result_v2['contract']}")
            print(f"   Exchange     : {result_v2['source']}")
            print(f"   Chain        : {result_v2['chain_id']}")
            print(f"   Pair Address : {result_v2['primary_pair_address']}")
            print(f"   Volume 24h   : ${result_v2['primary_volume_24h']:,.0f}")
            print(f"   Total Vol    : ${result_v2['total_volume_24h']:,.0f}")
            print(f"   Liquidité    : ${result_v2['liquidity_usd']:,.0f}")
        else:
            print("\n❌ Aucune paire trouvée (v2)")
    else:
        print("\n⚠️ Impossible de tester v2 (v1 a échoué)")

    print("\n" + "=" * 100)
