"""
Twitter Worker V2 - Orchestrateur entre Playwright/Nitter et X Pro
Nettoyé des anciennes fonctions de scraping (maintenant dans twitter_playwright.py)
"""

import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any, cast

from peewee import DoesNotExist

from ..config import (
    PLAYWRIGHT_NITTER_CONFIG,
    TWITTER_CONFIG,
    TWITTERIO_CONFIG,
    TWITTER_SCRAPERS_CONFIG,
    TWSCRAPE_CONFIG,
)
from ..config_types import ScrapersDict
from ..database.models import ProjectFollowers, RawTweet, Token

logger = logging.getLogger(__name__)


# ============================================================================
# NORMALISATION DES DATES
# ============================================================================


def normalize_tweet_datetime(dt) -> str:
    """
    Normalise un datetime (avec ou sans timezone) en string ISO naive.

    Cette fonction garantit que toutes les dates insérées en base sont au format:
    "YYYY-MM-DD HH:MM:SS" (naive, sans timezone)

    Args:
        dt: datetime object (avec ou sans timezone), ou string ISO

    Returns:
        str: Date au format "YYYY-MM-DD HH:MM:SS"
    """
    if isinstance(dt, str):
        # Déjà une string, essayer de la parser
        try:
            from dateutil import parser
            dt_obj = parser.parse(dt)
        except Exception:
            # Si parsing échoue, retourner tel quel (probablement déjà au bon format)
            return dt
    else:
        dt_obj = dt

    # Si le datetime a une timezone, le convertir en naive local
    if dt_obj.tzinfo is not None:
        dt_obj = dt_obj.astimezone(None).replace(tzinfo=None)

    # Retourner au format "YYYY-MM-DD HH:MM:SS"
    return dt_obj.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================================
# GESTION DES INSTANCES NITTER (ROTATION INTELLIGENTE)
# ============================================================================


class NitterInstanceManager:
    """
    Gestionnaire intelligent des instances Nitter avec rotation et fallback.
    """

    def __init__(self, instances: list[str] | None = None):
        self.instances = instances or TWITTER_CONFIG["INSTANCES"]
        self.current_index = 0
        self.failed_instances: set[str] = set()
        self.last_success: dict[str, float] = {}
        self.retry_after: dict[str, float] = {}

        logger.info(f"🔧 NitterManager initialisé avec {len(self.instances)} instances")

    def get_current_instance(self) -> str:
        """Retourne l'instance courante en évitant celles en cooldown."""
        now = time.time()
        expired = [
            inst for inst, retry_time in self.retry_after.items() if now > retry_time
        ]
        for inst in expired:
            self.failed_instances.discard(inst)
            del self.retry_after[inst]
            logger.info(f"[OK] Instance {inst} retirée du cooldown")

        attempts = 0
        while attempts < len(self.instances):
            instance = self.instances[self.current_index]

            if instance not in self.failed_instances:
                return instance

            self.current_index = (self.current_index + 1) % len(self.instances)
            attempts += 1

        logger.warning("⚠️ Toutes les instances sont en cooldown, utilisation forcée")
        return self.instances[self.current_index]

    def rotate(self):
        """Passe à l'instance suivante."""
        self.current_index = (self.current_index + 1) % len(self.instances)

    def mark_failed(self, instance: str, cooldown_minutes: int = 10):
        """Marque une instance comme ratée et la met en cooldown."""
        self.failed_instances.add(instance)
        self.retry_after[instance] = time.time() + (cooldown_minutes * 60)
        logger.warning(
            f"❌ Instance {instance} en cooldown pour {cooldown_minutes} min"
        )
        self.rotate()

    def mark_success(self, instance: str):
        """Marque une instance comme fonctionnelle."""
        self.last_success[instance] = time.time()
        if instance in self.failed_instances:
            self.failed_instances.discard(instance)
            logger.info(f"[OK] Instance {instance} de nouveau opérationnelle")


# Instance globale du manager
nitter_manager = NitterInstanceManager()


# ============================================================================
# PARSING HTML NITTER (FONCTIONS UTILITAIRES)
# ============================================================================


def parse_stat_number(stat_text: str) -> int:
    """
    Parse un nombre depuis Nitter (gère les suffixes K, M, B).

    Exemples:
        "1,234" → 1234
        "12.5K" → 12500
        "1.2M" → 1200000

    Args:
        stat_text: Texte du nombre (ex: "12.5K")

    Returns:
        int: Nombre parsé
    """
    if not stat_text or stat_text.strip() == "":
        return 0

    stat_text = stat_text.strip().replace(",", "")

    # Multiplicateurs
    multipliers = {"K": 1000, "M": 1_000_000, "B": 1_000_000_000}

    for suffix, multiplier in multipliers.items():
        if suffix in stat_text.upper():
            try:
                number = float(stat_text.upper().replace(suffix, ""))
                return int(number * multiplier)
            except ValueError:
                logger.warning(f"⚠️ Impossible de parser '{stat_text}'")
                return 0

    # Pas de suffixe, juste un nombre
    try:
        return int(float(stat_text))
    except ValueError:
        logger.warning(f"⚠️ Impossible de parser '{stat_text}'")
        return 0


def parse_nitter_html_tweet(html_str: str) -> dict | None:
    """
    Parse un élément HTML de tweet Nitter et extrait les données.

    BUGFIX #3: Parsing robuste adapté à la structure réelle de Nitter (nitter.tiekoetter.com)
    La structure HTML peut varier selon l'instance, ce code gère les variations.

    Args:
        html_str: HTML d'un élément .timeline-item ou .tweet-body

    Returns:
        dict: Données du tweet normalisées ou None
    """
    import re
    from datetime import datetime

    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html_str, "html.parser")

        # ═══════════════════════════════════════════════════════════
        # 1. Extraire USERNAME
        # ═══════════════════════════════════════════════════════════
        username_elem = soup.find("a", class_="username")
        if not username_elem:
            logger.debug("❌ Pas de username trouvé")
            return None
        username = username_elem.get_text(strip=True).replace("@", "")

        # ═══════════════════════════════════════════════════════════
        # 2. Extraire TWEET ID (depuis le lien permanent)
        # ═══════════════════════════════════════════════════════════
        # Chercher le lien avec /status/ID
        tweet_link = soup.find("a", href=re.compile(r"/.*?/status/\d+"))
        if not tweet_link:
            logger.debug("❌ Pas de lien tweet trouvé")
            return None
        href = str(tweet_link.get("href", ""))
        tweet_id_match = re.search(r"/status/(\d+)", href)
        if not tweet_id_match:
            logger.debug(f"❌ Tweet ID non trouvé dans {href}")
            return None
        tweet_id = tweet_id_match.group(1)

        # ═══════════════════════════════════════════════════════════
        # 3. Extraire CONTENU DU TWEET
        # ═══════════════════════════════════════════════════════════
        content_elem = cast(Any, soup.find("div", class_="tweet-content"))
        content = content_elem.get_text(strip=True) if content_elem else ""

        # ═══════════════════════════════════════════════════════════
        # 4. Extraire DATE/HEURE (structure réelle de Nitter)
        # ═══════════════════════════════════════════════════════════
        posted_at = datetime.now()  # Default au cas où parsing échoue
        date_elem = cast(Any, soup.find("span", class_="tweet-date"))

        if date_elem:
            date_link = date_elem.find("a")
            if date_link:
                date_title = date_link.get("title", "")

                if date_title:
                    logger.debug(f"📅 Date brute trouvée: '{date_title}'")

                    try:
                        # Format Nitter standard: "Nov 25, 2025 · 3:45 PM UTC"
                        date_str = date_title.replace("UTC", "").strip()
                        posted_at = datetime.strptime(date_str, "%b %d, %Y · %I:%M %p")
                        logger.debug(f"✅ Date parsée (format standard): {posted_at}")

                    except ValueError:
                        try:
                            # Fallback: Format court "Nov 28, 2025" (sans heure)
                            posted_at = datetime.strptime(
                                date_title.split("·")[0].strip(), "%b %d, %Y"
                            )
                            logger.debug(f"✅ Date parsée (format court): {posted_at}")

                        except ValueError:
                            try:
                                # Fallback: Format court parfois utilisé "01/11/2025, 14:30:45"
                                posted_at = datetime.strptime(
                                    date_title, "%d/%m/%Y, %H:%M:%S"
                                )
                                logger.debug(
                                    f"✅ Date parsée (format court numérique): {posted_at}"
                                )

                            except ValueError as e:
                                logger.warning(
                                    f"⚠️ Impossible de parser la date '{date_title}' - "
                                    f"Utilisation de datetime.now() "
                                    f"(Erreur: {e})"
                                )
                                # Garder le default datetime.now() pour signaler l'erreur

        # ═══════════════════════════════════════════════════════════
        # 5. Extraire STATISTIQUES (structure réelle de Nitter)
        # ═══════════════════════════════════════════════════════════
        # Structure HTML réelle:
        # <div class="tweet-stats">
        #   <span class="tweet-stat">
        #     <div class="icon-container">
        #       <span class="icon-comment"></span> 1
        #     </div>
        #   </span>
        #   ...
        # </div>

        stats = {"replies": 0, "retweets": 0, "likes": 0, "quotes": 0, "views": 0}

        tweet_stats_div = cast(Any, soup.find("div", class_="tweet-stats"))
        if tweet_stats_div:
            stat_items = tweet_stats_div.find_all("span", class_="tweet-stat")
            logger.debug(f"📊 {len(stat_items)} éléments de stats trouvés")

            for stat in stat_items:
                try:
                    # Chercher l'icône (span avec class icon-*)
                    icon_span = stat.find("span", class_=re.compile(r"icon-"))
                    if not icon_span:
                        continue

                    icon_classes = icon_span.get("class", [])

                    # Extraire la valeur (le texte après l'icône dans le div)
                    icon_container = stat.find("div", class_="icon-container")
                    if not icon_container:
                        continue

                    # Le texte est directement dans le div, pas dans un sous-élément
                    full_text = icon_container.get_text(strip=True)
                    # Enlever le texte de l'icône (elle est vide) et garder le nombre
                    stat_value = parse_stat_number(full_text)

                    # Identifier le type de stat par la classe de l'icône
                    if "icon-comment" in icon_classes:
                        stats["replies"] = stat_value
                        logger.debug(f"  💬 Replies: {stat_value}")
                    elif "icon-retweet" in icon_classes:
                        stats["retweets"] = stat_value
                        logger.debug(f"  🔄 Retweets: {stat_value}")
                    elif "icon-heart" in icon_classes:
                        stats["likes"] = stat_value
                        logger.debug(f"  ❤️ Likes: {stat_value}")
                    elif "icon-quote" in icon_classes:
                        stats["quotes"] = stat_value
                        logger.debug(f"  💭 Quotes: {stat_value}")
                    elif "icon-views" in icon_classes or "icon-play" in icon_classes:
                        stats["views"] = stat_value
                        logger.debug(f"  👁️ Views: {stat_value}")

                except Exception as e:
                    logger.warning(f"⚠️ Erreur parsing stat: {e}")
                    continue
        else:
            logger.debug("⚠️ Pas de div.tweet-stats trouvé")

        # ═══════════════════════════════════════════════════════════
        # 6. Normaliser et retourner
        # ═══════════════════════════════════════════════════════════
        normalized = {
            "tweet_id": tweet_id,
            "username": username,
            "content": content,
            "posted_at": posted_at,
            "views": stats.get("views", 0),
            "likes": stats.get("likes", 0),
            "retweets": stats.get("retweets", 0),
            "quotes": stats.get("quotes", 0),
            "replies": stats.get("replies", 0),
        }

        logger.debug(
            f"✅ Tweet parsé: ID={tweet_id}, @{username}, {len(content)}c, "
            f"{stats['views']} vues, {stats['likes']} likes"
        )

        return normalized

    except Exception as e:
        logger.error(f"❌ Erreur critique parsing tweet HTML: {e}", exc_info=True)
        return None


# ============================================================================
# ORCHESTRATEUR - Choix entre Playwright et X Pro
# ============================================================================


def scrape_tweets_orchestrator(
    cashtag: str,
    since_date: str,
    until_date: str | None = None,
    mode: str = "auto",
    query_type: str = "Auto",
) -> list[dict]:
    """
    Orchestrateur intelligent avec système de priorité dynamique.

    Logique:
    - Si mode='auto': Essaie les scrapers dans l'ordre de PRIORITY_ORDER (config.py)
    - Si mode spécifique: Force l'utilisation de ce scraper

    Args:
        cashtag: CashTag à rechercher (ex: "$WAVES")
        since_date: Date de début (YYYY-MM-DD)
        until_date: Date de fin (YYYY-MM-DD). Si None, récupère jusqu'à maintenant.
        mode: 'auto', 'playwright', 'twscrape'
        query_type: TwitterIO queryType - "Auto", "Both", "Latest", "Top", "Media"

    Returns:
        list: Tweets récupérés
    """
    # Configuration des méthodes disponibles
    scrapers: ScrapersDict = cast(
        ScrapersDict,
        {
            "twitterio": {
                "config": TWITTERIO_CONFIG,
                "import_path": "twitterio_scraper",
                "function": "search_tweets_with_fallback",
                "name": "TwitterAPI.io",
                "icon": "🔷",
            },
            "playwright": {
                "config": PLAYWRIGHT_NITTER_CONFIG,
                "import_path": "nitter_playwright",
                "function": "search_tweets",
                "name": "Playwright/Nitter",
                "icon": "🌐",
            },
            "twscrape": {
                "config": TWSCRAPE_CONFIG,
                "import_path": "twscrape",
                "function": "search_tweets",
                "name": "Twscrape",
                "icon": "🔶",
            },
        },
    )
    # Mode manuel: force une méthode spécifique
    if mode != "auto":
        methods_to_try = [mode]
    else:
        # Mode auto: trie par priorité (1 = premier, 2 = second, etc.)
        # TWSCRAPE DÉSACTIVÉ pour recherches historiques (inutile, utiliser UNIQUEMENT pour 24h)
        priority_order = TWITTER_SCRAPERS_CONFIG.get("PRIORITY_ORDER", {})
        methods_to_try = sorted(
            priority_order.keys(), key=lambda x: priority_order.get(x, 999)
        )

    logger.info(f"🎯 Orchestrateur: Mode={mode}, Ordre={methods_to_try}")

    # Essayer chaque méthode dans l'ordre de priorité
    for method_name in methods_to_try:
        if method_name not in scrapers:
            logger.warning(f"⚠️ Méthode inconnue: {method_name}")
            continue

        scraper = scrapers[method_name]

        # Vérifier si activé
        if not scraper["config"].get("ENABLED", method_name == "playwright"):
            logger.debug(f"⏭️ {scraper['name']} désactivé, skip")
            continue

        try:
            # Import dynamique
            module = __import__(
                f"src.scrapers.{scraper['import_path']}", fromlist=[scraper["function"]]
            )
            search_function = getattr(module, scraper["function"])

            logger.info(f"{scraper['icon']} Tentative {scraper['name']}")

            # Pour TwitterIO, passer les paramètres supplémentaires (query_type, fallback_threshold)
            if method_name == "twitterio":
                tweets = cast(
                    list[dict],
                    search_function(
                        cashtag,
                        since_date,
                        until_date,
                        query_type=query_type,
                        fallback_threshold=TWITTERIO_CONFIG["FALLBACK_THRESHOLD"],
                    ),
                )
            else:
                tweets = cast(list[dict], search_function(cashtag, since_date, until_date))

            if tweets:
                logger.info(f"✅ {scraper['name']}: {len(tweets)} tweets récupérés")
                return tweets
            logger.warning(f"⚠️ {scraper['name']}: 0 tweets, essai suivant...")

        except Exception as e:
            logger.error(f"❌ Erreur {scraper['name']}: {e}")
            continue

    logger.error("❌ Tous les scrapers ont échoué")
    return []


def scrape_profile_orchestrator(username: str) -> dict | None:
    """
    Orchestrateur pour récupération de profil.

    Priorité: Playwright/Nitter > Twscrape > X Pro

    Args:
        username: Nom d'utilisateur Twitter (sans @)

    Returns:
        dict: Informations du profil
    """
    # Priorité 1: Playwright/Nitter (gratuit)
    if PLAYWRIGHT_NITTER_CONFIG.get("ENABLED", True):
        from .nitter_playwright import get_profile

        logger.info("🌐 Récupération profil via Playwright")
        profile = get_profile(username)

        if profile and profile.get("followers", 0) > 0:
            logger.info(f"✅ Profil: {profile['followers']:,} followers")
            return profile
        logger.warning("⚠️ Playwright: Profil invalide ou vide")

    # Priorité 2: Twscrape (backup)
    if TWSCRAPE_CONFIG.get("ENABLED", False):
        try:
            from .twscrape import get_profile

            logger.info("🔷 Fallback sur Twscrape pour le profil")
            profile = get_profile(username)

            if profile:
                logger.info(f"✅ Twscrape: {profile.get('followers', 0):,} followers")
                return profile
        except Exception as e:
            logger.error(f"❌ Erreur Twscrape profil: {e}")
    logger.error(f"❌ Impossible de récupérer le profil @{username}")
    return None


# ============================================================================
# CALCUL DU SCORE D'IMPACT
# ============================================================================


def calculate_tweet_impact_score(tweet_data: dict) -> float:
    """
    Calcule le score d'impact d'un tweet selon la formule pondérée.
    Impact = log10(Views + (Likes × 5) + (RTs × 20) + (Quotes × 15) + (Replies × 2) + 1)

    Args:
        tweet_data: Données du tweet

    Returns:
        float: Score d'impact calculé
    """
    stats = tweet_data.get("stats", {})
    weights = TWITTER_CONFIG["WEIGHTS"]

    views = stats.get("views", stats.get("view", 0))
    likes = stats.get("likes", 0)
    retweets = stats.get("retweets", 0)
    quotes = stats.get("quotes", 0)
    replies = stats.get("replies", 0)

    # Si pas de vues, estimation basée sur l'engagement
    if views == 0:
        estimated_views = (
            likes * TWITTER_CONFIG["ESTIMATED_VIEWS_PER_LIKE"] + retweets * 500
        )
        raw_score = estimated_views
    else:
        raw_score = (
            views * weights["VIEW"]
            + likes * weights["LIKE"]
            + retweets * weights["RETWEET"]
            + quotes * weights.get("QUOTE", 15.0)
            + replies * weights.get("REPLY", 2.0)
        )

    # Transformation logarithmique
    return math.log10(raw_score + 1)



# ============================================================================
# STOCKAGE ET MISE À JOUR EN BASE DE DONNÉES
# ============================================================================


def store_or_update_tweet(token: Token, tweet_data: dict) -> tuple[RawTweet, bool]:
    """
    Stocke ou met à jour un tweet en base de données.

    Args:
        token: Token concerné
        tweet_data: Données normalisées du tweet

    Returns:
        tuple: (RawTweet, is_new)
    """
    try:
        tweet = RawTweet.get(RawTweet.tweet_id == tweet_data["tweet_id"])
        is_new = False

        # Mise à jour
        tweet.views = tweet_data["views"]
        tweet.likes = tweet_data["likes"]
        tweet.retweets = tweet_data["retweets"]
        tweet.quotes = tweet_data["quotes"]
        tweet.replies = tweet_data["replies"]
        tweet.impact_score = tweet_data["impact_score"]
        tweet.impact_rate = tweet_data["impact_rate"]
        tweet.last_updated = datetime.now()
        tweet.save()

        logger.debug(f"🔄 Tweet mis à jour: {tweet_data['tweet_id']}")

    except DoesNotExist:
        tweet = RawTweet.create(
            token=token,
            tweet_id=tweet_data["tweet_id"],
            author=tweet_data["author"],
            content=tweet_data["content"],
            posted_at=tweet_data["posted_at"],
            views=tweet_data["views"],
            likes=tweet_data["likes"],
            retweets=tweet_data["retweets"],
            quotes=tweet_data["quotes"],
            replies=tweet_data["replies"],
            impact_score=tweet_data["impact_score"],
            impact_rate=tweet_data["impact_rate"],
        )
        is_new = True

        logger.debug(f"✨ Nouveau tweet créé: {tweet_data['tweet_id']}")

    return tweet, is_new


def store_followers_count(
    token: Token, followers_count: int, following_count: int | None = None
):
    """
    Stocke le nombre de followers du projet dans l'historique.

    Args:
        token: Token concerné
        followers_count: Nombre de followers
        following_count: Nombre d'abonnements
    """
    try:
        ProjectFollowers.create(
            token=token,
            followers_count=followers_count,
            following_count=following_count,
        )
        logger.info(
            f"👥 Followers enregistrés: {followers_count:,} pour {token.cashtag}"
        )
    except Exception as e:
        logger.error(f"❌ Erreur stockage followers: {e}")


# ============================================================================
# MODE 1: BACKFILL 7 JOURS (CROISIÈRE)
# ============================================================================


def fetch_history_7d(
    token: Token,
    project_username: str | None = None,
    force: bool = False,
    mode: str = "auto",
    custom_since: str | None = None,
    custom_until: str | None = None,
    use_smart_gaps: bool = True,
    query_type: str = "Auto",
    split_by_day: bool = True,  # 🆕 Par défaut activé
) -> bool:
    """
    MODE 1 - Scraping initial sur 7 jours (BACKFILL).

    Version simplifiée utilisant BackfillFetchStrategy (Pattern Strategy).
    Réduit de 261 → ~50 lignes grâce à la mutualisation du code.

    Args:
        token: Token à analyser
        project_username: Username Twitter du projet (sans @)
        force: Force le backfill
        mode: Mode de scraping ('auto', 'playwright', 'twscrape')
        custom_since: Date de début personnalisée (YYYY-MM-DD)
        custom_until: Date de fin personnalisée (YYYY-MM-DD)
        use_smart_gaps: Active la détection intelligente des gaps (défaut: True)
        query_type: TwitterIO queryType - "Auto", "Both", "Latest", "Top", "Media"
        split_by_day: Découpe automatiquement les plages multi-jours (défaut: True) 🆕

    Returns:
        bool: True si succès
    """
    from .twitter_fetch_strategies import BackfillFetchStrategy

    if not force and token.status != "PENDING":
        logger.info(f"⚠️ Backfill déjà effectué pour {token.cashtag}")
        return True

    logger.info(f"🚀 MODE 1 (BACKFILL) - 7j pour {token.cashtag}")

    # Utilisation de la stratégie pour éliminer la duplication
    strategy = BackfillFetchStrategy(
        token=token,
        project_username=project_username,
        split_by_day=split_by_day,  # 🆕
        custom_since=custom_since,
        custom_until=custom_until,
        use_smart_gaps=use_smart_gaps,
        days_back=TWITTER_CONFIG["BACKFILL_DAYS"],
        mode=mode,
        query_type=query_type,
    )

    result = strategy.execute()

    # Vérification de la couverture (logique spécifique au backfill)
    if result["success"] and result["new_tweets"] + result["updated_tweets"] > 0:
        from ..analysis.zscore_calc import check_data_coverage

        coverage_result = check_data_coverage(
            token, days_back=TWITTER_CONFIG["BACKFILL_DAYS"], coverage_threshold=0.80
        )

        if coverage_result["is_sufficient"]:
            logger.info(f"✅ {token.cashtag} - Backfill réussi avec couverture suffisante")
            return True
        else:
            logger.warning(f"⚠️ {token.cashtag} - Couverture insuffisante")
            return False
    elif result["new_tweets"] + result["updated_tweets"] == 0:
        logger.error(f"❌ Aucun tweet pour {token.cashtag}")
        return False
    else:
        return result["success"]


# ============================================================================
# MODE 2: MISE À JOUR RÉGULIÈRE (15 MIN)
# ============================================================================


def fetch_latest_updates(
    token: Token,
    project_username: str | None = None,
    lookback_hours: int = 1,
    update_active_tweets_hours: int = 12,
    use_twscrape_for_24h: bool = True,
) -> dict:
    """
    MODE 2 - Mise à jour régulière (15 min).

    Version simplifiée utilisant RegularUpdateStrategy (Pattern Strategy).
    Réduit de 126 → ~15 lignes grâce à la mutualisation du code.

    Args:
        token: Token à analyser
        project_username: Username du projet
        lookback_hours: Heures en arrière pour nouveaux tweets
        update_active_tweets_hours: Heures en arrière pour MàJ stats (non utilisé actuellement)
        use_twscrape_for_24h: Force twscrape pour recherches < 24h (non utilisé actuellement)

    Returns:
        dict: Statistiques de la mise à jour
    """
    from .twitter_fetch_strategies import RegularUpdateStrategy

    strategy = RegularUpdateStrategy(
        token=token,
        project_username=project_username,
        lookback_hours=lookback_hours,
        update_active_tweets_hours=update_active_tweets_hours,
    )

    return strategy.execute()


def fetch_croisiere_updates(
    token: Token, project_username: str | None = None, lookback_minutes: int = 60
) -> dict:
    """
    MODE CROISIERE - Mise à jour lente (1h).

    Version simplifiée utilisant CroisiereUpdateStrategy (Pattern Strategy).
    Réduit de 109 → ~15 lignes grâce à la mutualisation du code.

    Args:
        token: Token à analyser
        project_username: Username du projet
        lookback_minutes: Minutes en arrière pour nouveaux tweets (défaut: 60)

    Returns:
        dict: Statistiques de la mise à jour
    """
    from .twitter_fetch_strategies import CroisiereUpdateStrategy

    strategy = CroisiereUpdateStrategy(
        token=token,
        project_username=project_username,
        lookback_minutes=lookback_minutes,
    )

    return strategy.execute()


# ============================================================================
# FONCTION PRINCIPALE APPELÉE PAR LE SCHEDULER
# ============================================================================


def update_token_social_data(
    token: Token,
    mode: str = "regular",
    project_username: str | None = None,
    lookback_minutes: int | None = None,
) -> bool:
    """
    Point d'entrée principal pour la mise à jour des données sociales.

    Args:
        token: Token à analyser
        mode: Mode de mise à jour ('backfill', 'regular', 'croisiere')
        project_username: Username Twitter du projet (sans @)
        lookback_minutes: Fenêtre temporelle en minutes (utilisé pour REGULAR, CROISIERE)

    Returns:
        bool: True si succès
    """
    try:
        # Exécuter le scraping Twitter
        success = False

        if mode == "backfill":
            success = fetch_history_7d(token, project_username)

        elif mode == "croisiere":
            # Mode CROISIERE : lookback de 60 min par défaut
            lookback = lookback_minutes if lookback_minutes else 60
            stats = fetch_croisiere_updates(
                token, project_username, lookback_minutes=lookback
            )
            success = stats["success"]

        elif mode == "regular":
            # Mode REGULAR : lookback de 15 min par défaut, update scores 12h
            lookback_hours = (lookback_minutes / 60.0) if lookback_minutes else 1
            stats = fetch_latest_updates(
                token,
                project_username,
                lookback_hours=int(lookback_hours),
                update_active_tweets_hours=12,
            )
            success = stats["success"]

        else:
            logger.error(f"❌ Mode inconnu: {mode}")
            return False

        # Si scraping réussi, déclencher l'agrégation incrémentale
        if success:
            try:
                from ..analysis.metric_aggregator import MetricAggregator

                aggregator = MetricAggregator()

                logger.info(
                    f"🔄 Déclenchement agrégation incrémentale: {token.cashtag}"
                )
                result = aggregator.orchestrate_token_incremental(token)

                if result["status"] in ["SUCCESS", "NO_ACTION_NEEDED"]:
                    logger.info(f"✅ Agrégation terminée: {result['status']}")
                elif result["status"] == "PARTIAL":
                    logger.warning(f"⚠️  Agrégation partielle: {result['status']}")
                else:
                    logger.error(f"❌ Agrégation échouée: {result['status']}")

            except Exception as e:
                logger.error(f"❌ Erreur agrégation (non-bloquant): {e}")
                # Ne pas faire échouer le scraping si l'agrégation échoue

        return success

    except Exception as e:
        logger.error(f"❌ Erreur update_token_social_data: {e}")
        import traceback

        traceback.print_exc()
        return False


# ============================================================================
# UTILITAIRES
# ============================================================================


# ============================================================================
# JOB DE BACKFILL POUR APSCHEDULER
# ============================================================================


def start_backfill_job(
    token: Token,
    on_complete_callback=None,
    mode: str = "auto",
    custom_since: str | None = None,
    custom_until: str | None = None,
    use_smart_gaps: bool = True,
    query_type: str = "Auto",
) -> bool:
    """
    Fonction appelée par APScheduler pour démarrer le backfill d'un token.

    Cette fonction:
    1. Lance le backfill de 7 jours (avec smart gap detection optionnel)
    2. Si succès, appelle le callback (généralement pour passer en mode REGULAR)
    3. Si échec, laisse le token en PENDING

    Args:
        token: Token à backfiller
        on_complete_callback: Fonction à appeler en cas de succès (signature: callback(token))
        mode: Mode de scraping ('auto', 'playwright', 'twscrape')
        custom_since: Date de début personnalisée (YYYY-MM-DD)
        custom_until: Date de fin personnalisée (YYYY-MM-DD)
        use_smart_gaps: Active la détection intelligente des gaps (défaut: True)
        query_type: TwitterIO queryType - "Auto", "Both", "Latest", "Top", "Media"

    Returns:
        bool: True si le backfill a réussi
    """
    logger.info(f"🚀 [BACKFILL JOB] Démarrage pour {token.cashtag}")

    try:
        # Récupérer le username Twitter depuis le lien si disponible
        project_username = None
        if token.twitter_link:
            # Extraire le username depuis l'URL (ex: https://twitter.com/username)
            parts = token.twitter_link.rstrip("/").split("/")
            if len(parts) > 0:
                project_username = parts[-1]
                logger.info(f"📱 Username détecté: @{project_username}")

        # Lancer le backfill
        success = fetch_history_7d(
            token,
            project_username=project_username,
            force=True,
            mode=mode,
            custom_since=custom_since,
            custom_until=custom_until,
            use_smart_gaps=use_smart_gaps,
            query_type=query_type,
        )

        if success:
            logger.info(f"✅ [BACKFILL JOB] Réussi pour {token.cashtag}")

            # Appeler le callback pour passer en mode REGULAR
            if on_complete_callback:
                logger.info("🔄 [BACKFILL JOB] Activation du monitoring régulier...")
                on_complete_callback(token)
            else:
                logger.warning("⚠️ Aucun callback fourni, le token reste en PENDING")

            return True
        logger.error(f"❌ [BACKFILL JOB] Échec pour {token.cashtag} - Reste en PENDING")
        return False

    except Exception as e:
        logger.error(
            f"❌ [BACKFILL JOB] Erreur critique pour {token.cashtag}: {e}",
            exc_info=True,
        )
        return False
