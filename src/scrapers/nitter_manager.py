"""
Nitter Manager - Gestion des instances Nitter et parsing HTML
Déplacé depuis twitter_worker.py (lignes 80-372)
"""

import logging
import re
import time
from datetime import datetime
from typing import Any, cast

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# ============================================================================
# GESTION DES INSTANCES NITTER (ROTATION INTELLIGENTE)
# ============================================================================


class NitterInstanceManager:
    """
    Gestionnaire intelligent des instances Nitter avec rotation et fallback.
    """

    def __init__(self, instances: list[str] | None = None):
        from ..config import TWITTER_CONFIG

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
