"""
Twitter Fetch Strategies - Pattern Strategy pour éliminer la duplication

Remplace la logique dupliquée dans fetch_history_7d(), fetch_latest_updates(), fetch_croisiere_updates()
Utilise le Template Method Pattern pour partager le code commun.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)


# ============================================================================
# CLASSE DE BASE - TEMPLATE METHOD PATTERN
# ============================================================================


class TweetFetchStrategy(Protocol):
    """
    Classe de base pour toutes les stratégies de fetching.

    Utilise le Template Method Pattern pour éviter la duplication de code
    entre les 4 modes de fetching (backfill, regular, aggressive, croisiere).

    Chaque sous-classe doit implémenter :
    - get_scrape_ranges() : Retourne les plages de dates à scraper
    - should_update_profile() : Décide si on met à jour le profil Twitter
    """

    def __init__(self, token, project_username: str | None = None):
        """
        Initialise la stratégie.

        Args:
            token: Instance du modèle Token
            project_username: Username Twitter du projet (sans @)
        """
        self.token = token
        self.project_username = project_username
        self.stats = {
            "new_tweets": 0,
            "updated_tweets": 0,
            "followers_change": 0,
            "success": False,
        }

    def execute(self) -> dict:
        """
        Template method - Orchestration principale du fetching.

        Cette méthode définit le squelette de l'algorithme commun à tous les modes :
        1. Mise à jour du profil (optionnel)
        2. Scraping par plages de dates
        3. Normalisation et stockage des tweets
        4. Post-processing (optionnel)

        Returns:
            dict: Statistiques de l'exécution
        """
        from .twitter_normalizer import process_and_store_tweets
        from .twitter_profile_manager import update_token_profile
        from .twitter_worker import scrape_tweets_orchestrator

        start_time = time.time()

        try:
            # 1. Mise à jour du profil (si nécessaire)
            if self.should_update_profile():
                logger.info(f"👤 Mise à jour du profil @{self.project_username}")
                profile_stats = update_token_profile(self.token, self.project_username)
                self.stats["followers_change"] = profile_stats.get("followers_change", 0)

            # 2. Déterminer les plages à scraper (logique spécifique à chaque stratégie)
            scrape_ranges = self.get_scrape_ranges()

            if not scrape_ranges:
                logger.warning("⚠️ Aucune plage de dates à scraper")
                self.stats["success"] = True  # Pas d'erreur, juste rien à faire
                return self.stats

            # 3. Scraping et traitement pour chaque plage
            logger.info(f"📊 {len(scrape_ranges)} plage(s) à scraper")

            for idx, (since_date, until_date) in enumerate(scrape_ranges, 1):
                until_display = until_date if until_date else "maintenant"
                logger.info(f"🔍 Plage {idx}/{len(scrape_ranges)}: {since_date} → {until_display}")

                # Scraping
                tweets = scrape_tweets_orchestrator(
                    cashtag=self.token.cashtag,
                    since_date=since_date,
                    until_date=until_date,
                    mode=self.get_scrape_mode(),
                    query_type=self.get_query_type(),
                )

                if not tweets:
                    logger.info(f"   ℹ️ Aucun tweet trouvé pour cette plage")
                    continue

                # Normalisation et stockage (élimine la duplication!)
                new_count, updated_count = process_and_store_tweets(tweets, self.token)
                self.stats["new_tweets"] += new_count
                self.stats["updated_tweets"] += updated_count

                logger.info(
                    f"   ✅ Traités: {new_count} nouveaux, {updated_count} mis à jour"
                )

            # 4. Post-processing optionnel (hook pour les sous-classes)
            self.post_process()

            # 5. Stats finales
            duration = time.time() - start_time
            logger.info(
                f"✅ Fetch terminé en {duration:.1f}s - "
                f"{self.stats['new_tweets']} nouveaux tweets, "
                f"{self.stats['updated_tweets']} mis à jour"
            )

            self.stats["success"] = True
            return self.stats

        except Exception as e:
            logger.error(f"❌ Erreur durant le fetch: {e}", exc_info=True)
            self.stats["success"] = False
            return self.stats

    def get_scrape_ranges(self) -> list[tuple[str, str | None]]:
        """
        Retourne les plages de dates à scraper.

        Cette méthode doit être implémentée par les sous-classes.

        Returns:
            list: Liste de tuples (since_date, until_date)
                  until_date peut être None pour "jusqu'à maintenant"
        """
        raise NotImplementedError("Les sous-classes doivent implémenter get_scrape_ranges()")

    def should_update_profile(self) -> bool:
        """
        Décide si on doit mettre à jour le profil Twitter.

        Par défaut: True si un username est fourni.
        Les sous-classes peuvent override pour une logique plus complexe.
        """
        return bool(self.project_username)

    def get_scrape_mode(self) -> str:
        """
        Retourne le mode de scraping à utiliser.

        Par défaut: "auto" (laisse l'orchestrator décider)
        Les sous-classes peuvent override pour forcer un mode spécifique.
        """
        return "auto"

    def get_query_type(self) -> str:
        """
        Retourne le query_type pour TwitterIO.

        Par défaut: "Auto"
        Les sous-classes peuvent override pour forcer un type spécifique.
        """
        return "Auto"

    def post_process(self):
        """
        Hook optionnel pour post-processing après le scraping.

        Par défaut: ne fait rien.
        Les sous-classes peuvent override pour ajouter de la logique.
        """
        pass


# ============================================================================
# STRATÉGIE 1: BACKFILL (Mode historique 7 jours)
# ============================================================================


class BackfillFetchStrategy(TweetFetchStrategy):
    """
    Stratégie pour le backfill historique (7 jours par défaut).

    Fonctionnalités:
    - Dates personnalisées (custom_since/custom_until)
    - Smart gap detection (évite de re-scraper des périodes déjà couvertes)
    - Backfill classique (X jours en arrière)
    - Découpage automatique jour-par-jour (NEW!)
    """

    def __init__(
        self,
        token,
        project_username: str | None = None,
        split_by_day: bool = True,  # 🆕 Par défaut activé
        custom_since: str | None = None,
        custom_until: str | None = None,
        use_smart_gaps: bool = True,
        days_back: int = 7,
        mode: str = "auto",  # Mode de scraping
        query_type: str = "Auto",  # TwitterIO query type
    ):
        super().__init__(token, project_username)
        self.split_by_day = split_by_day
        self.custom_since = custom_since
        self.custom_until = custom_until
        self.use_smart_gaps = use_smart_gaps
        self.days_back = days_back
        self.mode = mode
        self.query_type = query_type

        logger.info(
            f"🔙 BACKFILL pour {token.cashtag} - "
            f"split_by_day={split_by_day}, smart_gaps={use_smart_gaps}, mode={mode}"
        )

    def get_scrape_ranges(self) -> list[tuple[str, str | None]]:
        """
        Détermine les plages de dates à scraper pour le backfill.

        Ordre de priorité:
        1. Dates personnalisées (custom_since/custom_until)
        2. Smart gap detection (si activé)
        3. Backfill classique (days_back jours en arrière)

        Puis découpe en jours individuels si split_by_day=True.
        """
        scrape_ranges: list[tuple[str, str | None]] = []

        # 1. Dates personnalisées (priorité absolue)
        if self.custom_since:
            scrape_ranges = [(self.custom_since, self.custom_until)]
            until_display = self.custom_until if self.custom_until else "maintenant"
            logger.info(f"📅 Dates personnalisées: {self.custom_since} → {until_display}")

        # 2. Smart gap detection
        elif self.use_smart_gaps:
            logger.info("🔍 Activation de la détection intelligente des gaps...")
            from ..analysis.gap_detector import get_scrape_date_ranges

            try:
                scrape_ranges, stats = get_scrape_date_ranges(
                    self.token,
                    lookback_days=self.days_back,
                    merge_threshold_hours=1,
                    safety_margin_hours=1,
                    min_tweets=1,
                    min_for_isolated=3,
                    hours_to_check=2,
                )

                if scrape_ranges:
                    logger.info("📊 Résultat gap detection:")
                    logger.info(f"   - Couverture actuelle: {stats['coverage_percentage']:.1f}%")
                    logger.info(f"   - Gaps détectés: {stats['total_gaps']}")
                    logger.info(f"   - Plages à scraper: {len(scrape_ranges)}")
                else:
                    logger.info("✅ Aucun gap détecté - Couverture complète")

            except Exception as e:
                logger.warning(f"⚠️ Gap detection échouée ({e}), fallback sur backfill classique")
                scrape_ranges = []

        # 3. Backfill classique (fallback)
        if not scrape_ranges:
            since_date = (datetime.now() - timedelta(days=self.days_back)).strftime("%Y-%m-%d")
            scrape_ranges = [(since_date, None)]
            logger.info(f"📅 Backfill classique: {self.days_back} jours ({since_date} → maintenant)")

        # 4. 🆕 Découpage jour-par-jour (si activé)
        if self.split_by_day:
            scrape_ranges = self._split_by_day(scrape_ranges)

        return scrape_ranges

    def _split_by_day(self, ranges: list[tuple[str, str | None]]) -> list[tuple[str, str]]:
        """
        Découpe les plages multi-jours en jours individuels.

        IMPORTANT: L'API Twitter utilise "until:" comme paramètre EXCLUSIF.
        Le code de twitterio_scraper.py ajoute automatiquement +1 jour à until_date.
        Pour éviter les chevauchements, on génère des plages où until = since.

        Exemple:
            Input:  [("2025-12-15", "2025-12-18")]
            Output: [("2025-12-15", "2025-12-15"),  # Devient until:2025-12-16 (couvre le 15)
                     ("2025-12-16", "2025-12-16"),  # Devient until:2025-12-17 (couvre le 16)
                     ("2025-12-17", "2025-12-17")]  # Devient until:2025-12-18 (couvre le 17)

        Args:
            ranges: Liste de plages (since, until)

        Returns:
            Liste de plages découpées jour-par-jour (sans chevauchement)
        """
        daily_ranges: list[tuple[str, str]] = []

        for since_date, until_date in ranges:
            # Si until_date est None, on prend aujourd'hui
            if until_date is None:
                until_date = datetime.now().strftime("%Y-%m-%d")

            try:
                since_dt = datetime.strptime(since_date, "%Y-%m-%d")
                until_dt = datetime.strptime(until_date, "%Y-%m-%d")
            except ValueError as e:
                logger.warning(f"⚠️ Format de date invalide ({e}), plage conservée telle quelle")
                daily_ranges.append((since_date, until_date))
                continue

            # Si la plage fait 1 jour ou moins, pas besoin de découper
            # Cas 1 : until = since (déjà 1 jour après ajustement +1)
            # Cas 2 : until = since + 1 jour (1 jour exact)
            if (until_dt - since_dt).days <= 1:
                # Utiliser same-day pour éviter chevauchement après ajustement +1
                daily_ranges.append((since_date, since_date))
                continue

            # Découper en jours individuels (until = since pour chaque jour)
            current = since_dt
            while current < until_dt:
                # Générer une plage où until = since (même jour)
                # Après l'ajustement +1 dans twitterio_scraper, cela couvrira exactement ce jour
                daily_ranges.append((
                    current.strftime("%Y-%m-%d"),
                    current.strftime("%Y-%m-%d")  # ← Changement clé : même jour !
                ))
                current = current + timedelta(days=1)

        if len(daily_ranges) > len(ranges):
            logger.info(f"📅 Découpage jour-par-jour: {len(ranges)} plage(s) → {len(daily_ranges)} jour(s)")

        return daily_ranges

    def get_scrape_mode(self) -> str:
        """Override pour retourner le mode choisi par l'utilisateur."""
        return self.mode

    def get_query_type(self) -> str:
        """Override pour retourner le query_type choisi par l'utilisateur."""
        return self.query_type


# ============================================================================
# STRATÉGIE 2: REGULAR UPDATE (Mode surveillance régulière)
# ============================================================================


class RegularUpdateStrategy(TweetFetchStrategy):
    """
    Stratégie pour les mises à jour régulières (toutes les 15 min par défaut).

    Fonctionnalités:
    - Scraping des tweets récents (lookback configurable)
    - Mise à jour des stats des tweets actifs (dernières 12h)
    """

    def __init__(
        self,
        token,
        project_username: str | None = None,
        lookback_hours: int = 1,
        update_active_tweets_hours: int = 12,
    ):
        super().__init__(token, project_username)
        self.lookback_hours = lookback_hours
        self.update_active_tweets_hours = update_active_tweets_hours

        logger.info(
            f"🔄 REGULAR UPDATE pour {token.cashtag} - "
            f"lookback={lookback_hours}h, update_active={update_active_tweets_hours}h"
        )

    def get_scrape_ranges(self) -> list[tuple[str, str | None]]:
        """Retourne une seule plage: les X dernières heures jusqu'à maintenant."""
        since_date = (datetime.now() - timedelta(hours=self.lookback_hours)).strftime("%Y-%m-%d")
        return [(since_date, None)]

    def post_process(self):
        """
        Post-processing pour le mode regular.

        Note: L'ancien code ne faisait pas de mise à jour spéciale des tweets actifs.
        Le scraping des nouveaux tweets suffit pour maintenir les stats à jour.
        """
        pass


# ============================================================================
# STRATÉGIE 3: CROISIERE UPDATE (Mode économique)
# ============================================================================


class CroisiereUpdateStrategy(TweetFetchStrategy):
    """
    Stratégie pour le mode croisière (surveillance lente, 1h par défaut).

    Mode le plus économique en ressources:
    - Récupère uniquement les nouveaux tweets (pas de mise à jour des stats)
    - Pas de mise à jour du profil par défaut
    """

    def __init__(
        self,
        token,
        project_username: str | None = None,
        lookback_minutes: int = 60,
    ):
        super().__init__(token, project_username)
        self.lookback_minutes = lookback_minutes

        logger.info(
            f"🚢 CROISIERE UPDATE pour {token.cashtag} - "
            f"lookback={lookback_minutes} min"
        )

    def get_scrape_ranges(self) -> list[tuple[str, str | None]]:
        """Retourne une seule plage: les X dernières minutes jusqu'à maintenant."""
        lookback_hours = self.lookback_minutes / 60.0
        since_date = (datetime.now() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d")
        return [(since_date, None)]

    def should_update_profile(self) -> bool:
        """
        En mode croisière, on ne met à jour le profil que si explicitement demandé.

        Override du comportement par défaut pour économiser les ressources.
        """
        # On pourrait ajouter une logique plus complexe ici
        # Par exemple: ne mettre à jour que toutes les X exécutions
        return bool(self.project_username)
