"""
Scraper Nitter avec Playwright
Plus fiable que requests, gère le JavaScript et évite les blocks
"""

import contextlib
import logging
import time
from datetime import datetime
from typing import Any, cast
from urllib.parse import quote

from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from ..config import PLAYWRIGHT_NITTER_CONFIG

logger = logging.getLogger(__name__)


# ============================================================================
# GESTIONNAIRE DE NAVIGATEUR PLAYWRIGHT
# ============================================================================


class NitterPlaywrightBrowser:
    """Gestionnaire du navigateur Playwright pour Nitter."""

    def __init__(self, headless: bool | None = None):
        """
        Args:
            headless (bool): Mode sans interface. Si None, utilise la config.
        """
        from playwright.sync_api import BrowserContext, Playwright

        self.headless = (
            headless if headless is not None else PLAYWRIGHT_NITTER_CONFIG["HEADLESS"]
        )
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self):
        """Context manager: démarrage du navigateur."""
        self.playwright = sync_playwright().start()

        logger.info(f"🌐 Démarrage navigateur Playwright (headless={self.headless})")

        # Lancer Chromium avec options
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled"
            ],  # Éviter détection bot
        )

        # Créer un contexte avec user agent
        self.context = self.browser.new_context(
            user_agent=PLAYWRIGHT_NITTER_CONFIG["USER_AGENT"],
            viewport=PLAYWRIGHT_NITTER_CONFIG["VIEWPORT"],
        )

        # Créer une page
        self.page = self.context.new_page()
        self.page.set_default_timeout(PLAYWRIGHT_NITTER_CONFIG["TIMEOUT"])
        self.page.set_default_navigation_timeout(
            PLAYWRIGHT_NITTER_CONFIG["NAVIGATION_TIMEOUT"]
        )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager: fermeture du navigateur."""
        if self.page:
            self.page.close()
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

        logger.info("🔒 Navigateur Playwright fermé")

    def goto(self, url: str) -> bool:
        """
        Navigue vers une URL avec attente intelligente.

        Args:
            url (str): URL cible

        Returns:
            bool: True si succès
        """
        try:
            if not self.page:
                logger.error("❌ Page non initialisée")
                return False

            logger.info(f"🔍 Navigation vers {url}")
            response = self.page.goto(url, wait_until="networkidle")

            if response and response.status == 200:
                # OPTIMISATION : Attendre que des éléments spécifiques soient chargés
                # Au lieu d'un sleep aveugle, on attend que la timeline soit visible
                try:
                    self.page.wait_for_selector("div.timeline", timeout=5000)
                    logger.debug("✅ Timeline chargée")
                except Exception:
                    # Si pas de timeline (profil vide par exemple), on continue
                    logger.debug("⚠️ Pas de timeline détectée (normal si profil vide)")

                return True
            logger.warning(f"⚠️ Status HTTP: {response.status if response else 'None'}")
            return False

        except PlaywrightTimeout:
            logger.error("❌ Timeout lors de la navigation")
            return False
        except Exception as e:
            logger.error(f"❌ Erreur navigation: {e}")
            return False

    def get_html(self) -> str:
        """Récupère le HTML de la page actuelle."""
        if not self.page:
            return ""
        return self.page.content()

    def click_load_more(self, since_date: str | None = None) -> bool:
        """
        Clique sur le bouton "Load more" si disponible.
        Attend intelligemment que les nouveaux tweets soient chargés.

        Args:
            since_date (str): Date de début pour vérification intelligente (YYYY-MM-DD)

        Returns:
            bool: True si le bouton a été cliqué
        """
        try:
            if not self.page:
                return False

            # Vérifier si on a atteint "No more items"
            if self.has_no_more_items(since_date):
                logger.info("✅ 'No more items' atteint")
                return False

            # BUGFIX: Scroll progressif pour atteindre le bouton "Load more"
            # Nitter charge dynamiquement et le bouton peut être tout en bas
            logger.debug("📜 Scroll progressif vers le bas...")

            for scroll_attempt in range(5):
                self.page.evaluate("window.scrollBy(0, 1000)")
                time.sleep(0.8)  # Attendre le chargement du contenu
                logger.debug(f"   Scroll {scroll_attempt + 1}/5")

            # Chercher le bouton "Load more"
            # Le div.show-more est APRÈS les tweets, donc chercher directement sans timeline-item
            load_more = self.page.query_selector(".show-more a")

            if not load_more:
                logger.debug("ℹ️ Pas de div.show-more a trouvé")
                return False

            # Vérifier la visibilité
            is_visible = load_more.is_visible()
            if not is_visible:
                logger.debug(
                    f"⚠️ Bouton Load more trouvé mais CACHÉ (is_visible={is_visible})"
                )
                return False

            # Vérifier que c'est "Load more" et pas "Load newest"
            text = load_more.text_content()
            logger.debug(f"📌 Bouton trouvé: '{text}'")

            if text and "newest" in text.lower():
                logger.debug("ℹ️ Bouton 'Load newest' trouvé, chercher le prochain")
                # Trouver tous les boutons et prendre le second si existe
                all_buttons = self.page.query_selector_all(".show-more a")
                if len(all_buttons) > 1:
                    load_more = all_buttons[1]
                    text = load_more.text_content()
                    logger.debug(f"📌 Utilisation du 2e bouton: '{text}'")
                else:
                    logger.debug("ℹ️ Aucun 'Load more' trouvé après 'Load newest'")
                    return False

            if not text or "more" not in text.lower():
                logger.debug(f"⚠️ Bouton ne contient pas 'more': {text}")
                return False

            # Compter les tweets avant le clic
            tweets_before = len(
                self.page.query_selector_all("div.timeline-item[data-username]")
            )
            logger.debug(f"📊 Tweets avant clic: {tweets_before}")

            logger.info("🔄 Clic sur 'Load more'")
            load_more.click()

            # OPTIMISATION : Attendre que de nouveaux tweets apparaissent
            # Au lieu de time.sleep(), on attend activement
            # Attendre que le réseau soit calme (max 5 secondes)
            with contextlib.suppress(Exception):
                self.page.wait_for_load_state("networkidle", timeout=5000)

            # Vérifier que de nouveaux tweets sont apparus
            max_wait = 10  # 10 tentatives max
            for _attempt in range(max_wait):
                tweets_after = len(
                    self.page.query_selector_all("div.timeline-item[data-username]")
                )
                if tweets_after > tweets_before:
                    logger.debug(
                        f"✅ Nouveaux tweets chargés: {tweets_before} → {tweets_after}"
                    )
                    return True

                # Attendre un peu avant de revérifier
                time.sleep(0.3)

            # Si après 3 secondes aucun nouveau tweet, on continue
            logger.debug("⚠️ Aucun nouveau tweet détecté après le clic")
            return True

        except Exception as e:
            logger.warning(f"⚠️ Erreur clic Load more: {e}")
            return False

    def check_tweet_dates_coverage(self, since_date: str) -> dict:
        """
        Vérifie si les tweets chargés couvrent bien la période demandée.

        Args:
            since_date (str): Date de début recherchée (YYYY-MM-DD)

        Returns:
            dict: {
                'oldest_tweet_date': datetime,
                'newest_tweet_date': datetime,
                'total_tweets': int,
                'looks_suspicious': bool,
                'reason': str
            }
        """
        from datetime import datetime

        try:
            if not self.page:
                return {
                    "oldest_tweet_date": None,
                    "newest_tweet_date": None,
                    "total_tweets": 0,
                    "looks_suspicious": True,
                    "reason": "Page non initialisée",
                }

            # Récupérer tous les tweets actuellement chargés
            tweet_items = self.page.query_selector_all(
                "div.timeline-item[data-username]"
            )

            if not tweet_items:
                return {
                    "oldest_tweet_date": None,
                    "newest_tweet_date": None,
                    "total_tweets": 0,
                    "looks_suspicious": True,
                    "reason": "Aucun tweet chargé",
                }

            # Parser les dates des tweets
            dates = []
            for item in tweet_items:
                date_elem = item.query_selector("span.tweet-date a")
                if date_elem:
                    date_title = date_elem.get_attribute("title")
                    if date_title:
                        try:
                            # Parser la date Nitter (format: "Nov 25, 2025 · 3:45 PM UTC")
                            # On parse juste la partie date
                            date_str = date_title.split("·")[0].strip()
                            tweet_date = datetime.strptime(date_str, "%b %d, %Y")
                            dates.append(tweet_date)
                        except Exception:
                            pass

            if not dates:
                return {
                    "oldest_tweet_date": None,
                    "newest_tweet_date": None,
                    "total_tweets": len(tweet_items),
                    "looks_suspicious": True,
                    "reason": "Impossible de parser les dates",
                }

            oldest = min(dates)
            newest = max(dates)

            # Parser la date cible
            target_date = datetime.strptime(since_date, "%Y-%m-%d")

            # Vérification : le tweet le plus vieux doit être proche de since_date
            # Tolérance : 2 jours (pour gérer les fuseaux horaires et petites imprécisions)
            days_gap = (oldest - target_date).days

            # Critères de suspicion
            is_suspicious = False
            reason = "Couverture normale"

            if days_gap > 2:
                is_suspicious = True
                reason = f"Tweet le plus vieux: {oldest.strftime('%Y-%m-%d')} (cible: {since_date}, écart: {days_gap} jours)"
            elif len(dates) < 5:
                is_suspicious = True
                reason = f"Très peu de tweets chargés ({len(dates)}), possible saturation Nitter"

            return {
                "oldest_tweet_date": oldest,
                "newest_tweet_date": newest,
                "total_tweets": len(dates),
                "looks_suspicious": is_suspicious,
                "reason": reason,
            }

        except Exception as e:
            logger.debug(f"Erreur vérification dates: {e}")
            return {
                "oldest_tweet_date": None,
                "newest_tweet_date": None,
                "total_tweets": 0,
                "looks_suspicious": True,
                "reason": f"Erreur: {e}",
            }

    def has_no_more_items(self, since_date: str | None = None) -> bool:
        """
        Vérifie si on a atteint la fin de la timeline ("No more items").
        Effectue une vérification intelligente des dates avant la validation manuelle.

        Args:
            since_date (str): Date de début recherchée (YYYY-MM-DD), pour vérification automatique

        Returns:
            bool: True si "No more items" est présent
        """
        try:
            if not self.page:
                return False

            # Chercher le message "No more items"
            timeline_end = self.page.query_selector("h2.timeline-end")

            if timeline_end:
                text = timeline_end.text_content()
                if text is not None and "no more" in text.lower():
                    # Vérification intelligente des dates si since_date fournie
                    if since_date and PLAYWRIGHT_NITTER_CONFIG.get(
                        "MANUAL_VALIDATION_NO_MORE_ITEMS", False
                    ):
                        date_check = self.check_tweet_dates_coverage(since_date)

                        logger.info("📅 Vérification automatique des dates:")
                        logger.info(
                            f"    - Tweets chargés: {date_check['total_tweets']}"
                        )
                        if date_check["oldest_tweet_date"]:
                            logger.info(
                                f"    - Tweet le plus vieux: {date_check['oldest_tweet_date'].strftime('%Y-%m-%d')}"
                            )
                            logger.info(f"    - Date cible: {since_date}")

                        if date_check["looks_suspicious"]:
                            # Dates suspectes → validation manuelle
                            logger.warning(f"⚠️  SUSPECT: {date_check['reason']}")
                            logger.info("⏸️  PAUSE MANUELLE pour vérification")
                            logger.info("    ")
                            logger.info(
                                "    🔹 Pour CONTINUER : Appuyez sur F8 ou cliquez 'Resume'"
                            )
                            logger.info("    🔹 Pour ARRÊTER : Fermez le navigateur")

                            # Pause interactive
                            self.page.pause()
                            logger.info("▶️  Reprise du scraping...")
                        else:
                            # Dates OK → pas de pause, continuer automatiquement
                            logger.info(
                                f"✅ Vérification dates OK: {date_check['reason']}"
                            )
                            logger.info("✅ 'No more items' validé automatiquement")

                    elif (
                        PLAYWRIGHT_NITTER_CONFIG.get(
                            "MANUAL_VALIDATION_NO_MORE_ITEMS", False
                        )
                        and not since_date
                    ):
                        # Pas de since_date mais validation manuelle activée → pause systématique
                        logger.info("⏸️  'No more items' détecté - PAUSE MANUELLE")
                        logger.info(
                            "    (Vérification automatique désactivée: pas de since_date)"
                        )
                        logger.info("    ")
                        logger.info(
                            "    🔹 Pour CONTINUER : Appuyez sur F8 ou cliquez 'Resume'"
                        )
                        logger.info("    🔹 Pour ARRÊTER : Fermez le navigateur")
                        self.page.pause()
                        logger.info("▶️  Reprise du scraping...")

                    return True

            return False

        except Exception as e:
            logger.debug(f"Erreur vérification 'No more items': {e}")
            return False


# ============================================================================
# FONCTIONS DE SCRAPING AVEC PLAYWRIGHT
# ============================================================================


def scrape_nitter_search_playwright(
    cashtag: str,
    since_date: str,
    until_date: str | None = None,
    nitter_instance: str | None = None,
    max_load_more: int = 3,
) -> list[dict]:
    """
    Scrape la recherche Nitter avec Playwright.
    Gère le bouton "Load more" pour charger tous les tweets.

    Args:
        cashtag (str): CashTag à rechercher (ex: "$WAVES")
        since_date (str): Date de début (YYYY-MM-DD)
        until_date (str, optional): Date de fin (YYYY-MM-DD). Si None, récupère jusqu'à aujourd'hui.
        nitter_instance (str): URL de l'instance Nitter
        max_load_more (int): Nombre max de clics sur "Load more"

    Returns:
        list: Liste des tweets parsés
    """
    # Import dynamique pour éviter les imports circulaires
    from . import twitter_worker

    parse_nitter_html_tweet = twitter_worker.parse_nitter_html_tweet

    # Encoder le cashtag
    encoded_cashtag = quote(cashtag)

    # Construire l'URL (until_date optionnel)
    search_url = (
        f"{nitter_instance}/search?f=tweets&q={encoded_cashtag}&since={since_date}"
    )

    # BUGFIX #1 : Ajouter 1 jour à until_date car le paramètre est EXCLUSIF
    # Exemple: until=2024-12-01 cherche AVANT le 1er décembre, pas jusqu'au 1er décembre
    # Solution: Si on veut les tweets du 1er décembre, il faut until=2024-12-02
    if until_date:
        from datetime import datetime, timedelta

        try:
            until_dt = datetime.strptime(until_date, "%Y-%m-%d")
            until_dt_plus_one = until_dt + timedelta(days=1)
            until_date_adjusted = until_dt_plus_one.strftime("%Y-%m-%d")
            search_url += f"&until={until_date_adjusted}"
            logger.debug(
                f"🔧 Until date ajustée: {until_date} → {until_date_adjusted} (paramètre exclusif)"
            )
        except ValueError:
            logger.warning(f"⚠️ Format de until_date invalide: {until_date}, ignorer")
    else:
        logger.debug(f"📅 Recherche depuis {since_date} jusqu'à maintenant")

        tweets: list[dict[str, Any]] = []
    try:
        with NitterPlaywrightBrowser() as browser:
            # Naviguer vers la page
            if not browser.goto(search_url):
                logger.error("❌ Échec de navigation")
                return []

            # Parser les tweets AVANT chaque Load more (Nitter utilise pagination)
            logger.info("📥 Chargement et parsing des tweets...")
            load_more_count = 0
            max_clicks = 100

            while load_more_count < max_clicks:
                # Parser la page actuelle AVANT de cliquer Load more
                html = browser.get_html()
                soup = BeautifulSoup(html, "html.parser")
                timeline_items = soup.find_all(
                    "div", class_="timeline-item", attrs={"data-username": True}
                )

                logger.debug(
                    f"   Page {load_more_count + 1}: {len(timeline_items)} tweets trouvés"
                )

                for item in timeline_items:
                    tweet_data = parse_nitter_html_tweet(str(item))
                    # Éviter les doublons (même ID)
                    if tweet_data and not any(
                        t["tweet_id"] == tweet_data["tweet_id"] for t in tweets
                    ):
                        tweets.append(tweet_data)

                # Essayer de cliquer Load more
                if browser.click_load_more(since_date):
                    load_more_count += 1
                    logger.info(
                        f"📥 Load more #{load_more_count} ({len(tweets)} tweets)"
                    )
                    time.sleep(0.5)  # Attendre le chargement
                else:
                    # Pas de Load more, on a tout
                    break

            if load_more_count >= max_clicks:
                logger.warning(f"⚠️ Limite de sécurité atteinte ({max_clicks} clics)")
            else:
                logger.info(
                    f"✅ Tous les tweets chargés ({load_more_count} clics, {len(tweets)} tweets au total)"
                )

    except Exception as e:
        logger.error(f"❌ Erreur scraping Playwright: {e}")
        import traceback

        traceback.print_exc()

    return tweets


def scrape_nitter_manual(
    cashtag: str,
    since_date: str,
    until_date: str | None = None,
    nitter_instance: str = "https://nitter.net",
) -> list[dict] | None:
    """
    Démarre un navigateur Playwright en mode non-headless pour le scraping manuel.
    Navigue automatiquement vers la page de recherche pour la période donnée.
    Permet à l'utilisateur de scroller manuellement, puis déclenche le scraping.
    Supporte le scraping itératif (page par page).

    Args:
        cashtag (str): CashTag à rechercher (ex: "$WAVES")
        since_date (str): Date de début (YYYY-MM-DD)
        until_date (str, optional): Date de fin (YYYY-MM-DD)
        nitter_instance (str): URL de l'instance Nitter

    Returns:
        list: Liste des tweets (dictionnaires) trouvés, ou None en cas d'échec.
    """
    # Import dynamique pour éviter les imports circulaires
    from . import twitter_worker

    parse_nitter_html_tweet = twitter_worker.parse_nitter_html_tweet

    p = None
    browser = None

    # Encoder le cashtag
    encoded_cashtag = quote(cashtag)

    # Construire l'URL
    search_url = (
        f"{nitter_instance}/search?f=tweets&q={encoded_cashtag}&since={since_date}"
    )
    if until_date:
        search_url += f"&until={until_date}"

    logger.info(
        f"🌐 [SCRAPE MANUEL] Démarrage du navigateur pour {cashtag} sur {nitter_instance}"
    )
    logger.info(f"🔗 URL cible : {search_url}")

    try:
        # Démarrage synchrone de Playwright
        p = sync_playwright().start()

        # Lancement forcé en non-headless (headless=False)
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        # Navigation initiale
        logger.info("⏳ Navigation automatique vers la page de recherche...")
        try:
            page.goto(
                search_url, timeout=60000
            )  # Timeout long pour laisser le temps de charger
        except Exception as e:
            logger.warning(
                f"⚠️ Erreur lors de la navigation automatique (peut-être normal si captcha): {e}"
            )
            logger.warning(
                "👉 Veuillez naviguer manuellement si la page ne s'est pas chargée."
            )

        logger.warning("✅ Navigateur ouvert sur la recherche.")
        logger.warning("👉 MODE ITÉRATIF ACTIVÉ :")
        logger.warning(
            "   1. Si l'instance bloque, changez l'URL manuellement (ex: nitter.net -> nitter.cz)."
        )
        logger.warning("   2. Gérez les captchas si nécessaire.")
        logger.warning("   3. Chargez une page de tweets.")
        logger.warning(
            "   4. Revenez ici et appuyez sur ENTREE pour scraper cette page."
        )
        logger.warning("   5. Répétez pour chaque page.")
        logger.warning("   6. Tapez 'stop' ou 'fin' pour terminer.")

        all_tweets = []
        seen_tweet_ids = set()

        while True:
            # Pause interactive
            user_input = (
                input(
                    "\n>>> [ACTION] Appuyez sur ENTREE pour scraper la vue actuelle (ou tapez 'stop' pour finir) : "
                )
                .strip()
                .lower()
            )

            if user_input in ["stop", "fin", "exit", "quit", "q"]:
                logger.info("🛑 Arrêt du scraping manuel demandé.")
                break

            current_url = page.url
            logger.info(f"📸 Extraction sur : {current_url}")

            # Récupération du HTML de la page actuelle
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            # L'extraction utilise les sélecteurs par défaut de Nitter (timeline-item)
            tweet_containers = soup.find_all("div", class_="timeline-item")

            if not tweet_containers:
                logger.warning(
                    "⚠️ Aucun conteneur de tweet trouvé sur cette vue (div.timeline-item)."
                )
                continue

            new_tweets_count = 0

            # Itération sur tous les conteneurs de tweets trouvés
            for container in tweet_containers:
                try:
                    # Utilisation de la fonction de parsing existante
                    # On passe le HTML du conteneur sous forme de string
                    tweet_data = parse_nitter_html_tweet(str(container))

                    if not tweet_data:
                        continue

                    tweet_id = tweet_data["tweet_id"]

                    # Éviter les doublons
                    if tweet_id in seen_tweet_ids:
                        continue

                    # Adapter le format pour correspondre à ce que attend le reste du code (si nécessaire)
                    # parse_nitter_html_tweet retourne :
                    # {'tweet_id', 'username', 'content', 'posted_at', 'views', 'likes', 'retweets', 'quotes', 'replies'}

                    # On ajoute les champs manquants pour la compatibilité
                    tweet_data["text"] = tweet_data["content"]
                    tweet_data["timestamp"] = tweet_data["posted_at"]
                    tweet_data["engagement"] = (
                        tweet_data["likes"] + tweet_data["retweets"]
                    )
                    tweet_data["cashtag"] = cashtag  # On remet le cashtag recherché
                    tweet_data["created_at"] = datetime.now()
                    tweet_data["updated_at"] = datetime.now()

                    all_tweets.append(tweet_data)
                    seen_tweet_ids.add(tweet_id)
                    new_tweets_count += 1

                except Exception as e:
                    logger.error(f"Erreur extraction tweet: {e}")
                    continue

            logger.info(
                f"✅ +{new_tweets_count} nouveaux tweets (Total unique: {len(all_tweets)})"
            )
            logger.info(
                "👉 Chargez la page suivante dans le navigateur, puis appuyez sur ENTREE ici."
            )

        logger.info(
            f"🎉 Scraping manuel terminé. Total final : {len(all_tweets)} tweets."
        )
        return all_tweets

    except PlaywrightTimeout as e:
        logger.error(f"❌ Playwright Timeout : {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Erreur critique lors du scraping manuel : {e}")
        import traceback

        traceback.print_exc()
        return None
    finally:
        if browser:
            logger.info("Fermeture du navigateur Playwright dans 2 secondes...")
            time.sleep(2)
            browser.close()
        if p:
            p.stop()


def scrape_nitter_profile_playwright(
    username: str, nitter_instance: str
) -> dict | None:
    """
    Scrape le profil Nitter avec Playwright.

    Args:
        username (str): Nom d'utilisateur Twitter (sans @)
        nitter_instance (str): URL de l'instance Nitter

    Returns:
        dict: Informations du profil ou None
    """
    # Import dynamique pour éviter imports circulaires
    from . import twitter_worker

    parse_stat_number = twitter_worker.parse_stat_number

    profile_url = f"{nitter_instance}/{username}"

    try:
        with NitterPlaywrightBrowser() as browser:
            # Naviguer vers le profil
            if not browser.goto(profile_url):
                logger.error("❌ Échec de navigation vers le profil")
                return None

            # Récupérer le HTML
            html = browser.get_html()

            # Parser le HTML
            soup = BeautifulSoup(html, "html.parser")

            # Trouver les statistiques (dans la structure fournie par l'utilisateur)
            profile_stats = soup.find("ul", class_="profile-statlist")

            if not profile_stats:
                logger.warning("⚠️ Statistiques de profil non trouvées")
                return None

            # Extraire les stats - Nouvelle approche basée sur la classe de chaque li
            stats = {}

            # Chercher chaque stat individuellement par classe
            for stat_class in ["posts", "following", "followers", "likes"]:
                stat_item = profile_stats.find("li", class_=stat_class)
                if stat_item:
                    stat_header = stat_item.find("span", class_="profile-stat-header")
                    stat_num = stat_item.find("span", class_="profile-stat-num")

                    if stat_header and stat_num:
                        key = stat_header.get_text(strip=True).lower()
                        value_text = stat_num.get_text(strip=True)
                        value = parse_stat_number(value_text)
                        stats[key] = value
                        logger.debug(f"   {key}: {value}")

            # Extraire le nom complet
            fullname_elem = soup.find("a", class_="profile-card-fullname")
            fullname = fullname_elem.get_text(strip=True) if fullname_elem else username

            profile_data = {
                "username": username,
                "fullname": fullname,
                "followers": stats.get("followers", 0),
                "following": stats.get("following", 0),
                "tweets": stats.get("tweets", 0),
            }

            logger.info(
                f"✅ Profil @{username}: {profile_data['followers']:,} followers, "
                f"{profile_data['following']:,} following"
            )

            return profile_data

    except Exception as e:
        logger.error(f"❌ Erreur scraping profil Playwright: {e}")
        import traceback

        traceback.print_exc()
        return None


# ============================================================================
# FONCTION WRAPPER AVEC RETRY ET ROTATION D'INSTANCES
# ============================================================================


def scrape_with_playwright_retry(scrape_func, max_retries: int = 3, **kwargs) -> Any:
    """
    Exécute une fonction de scraping Playwright avec retry et rotation d'instances.

    Args:
        scrape_func: Fonction de scraping à exécuter
        max_retries: Nombre de tentatives max
        **kwargs: Arguments pour la fonction

    Returns:
        Résultat du scraping ou None
    """
    from .twitter_worker import nitter_manager

    for attempt in range(max_retries):
        current_instance = nitter_manager.get_current_instance()

        try:
            # Ajouter l'instance aux kwargs
            kwargs["nitter_instance"] = current_instance

            # Exécuter la fonction
            result = scrape_func(**kwargs)

            # Marquer l'instance comme fonctionnelle
            nitter_manager.mark_success(current_instance)

            return result

        except Exception as e:
            logger.warning(
                f"⚠️ Tentative {attempt + 1}/{max_retries} échouée sur {current_instance}: {e}"
            )

            if attempt < max_retries - 1:
                nitter_manager.mark_failed(current_instance, cooldown_minutes=5)
                time.sleep(2 * (attempt + 1))
            else:
                logger.error(f"❌ Échec définitif après {max_retries} tentatives")
                return None

    return None


# ============================================================================
# EXPORT DES FONCTIONS PRINCIPALES
# ============================================================================


def search_tweets(
    cashtag: str, since_date: str, until_date: str | None = None
) -> list[dict]:
    """
    Recherche des tweets pour un cashtag avec Playwright.

    Args:
        cashtag (str): CashTag à rechercher
        since_date (str): Date de début (YYYY-MM-DD)
        until_date (str, optional): Date de fin (YYYY-MM-DD). Si None, récupère jusqu'à aujourd'hui.

    Returns:
        list: Liste des tweets
    """
    return cast(
        list[dict],
        scrape_with_playwright_retry(
            scrape_nitter_search_playwright,
            cashtag=cashtag,
            since_date=since_date,
            until_date=until_date,
            max_load_more=PLAYWRIGHT_NITTER_CONFIG["MAX_SCROLLS"],
        ),
    )


def get_profile(username: str) -> dict | None:
    """
    Récupère le profil d'un utilisateur avec Playwright.

    Args:
        username (str): Nom d'utilisateur (sans @)

    Returns:
        dict: Informations du profil
    """
    return cast(
        dict | None,
        scrape_with_playwright_retry(
            scrape_nitter_profile_playwright, username=username
        ),
    )
