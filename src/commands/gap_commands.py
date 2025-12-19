"""
Commandes CLI pour la gestion des gaps V2.

Commandes disponibles:
- gap_status: Affiche un résumé des gaps avec warnings
- gap_list: Liste tous les gaps d'un token
- gap_retry: Réessaye manuellement un gap
- gap_reset: Réinitialise l'historique d'un gap
"""

import logging
from datetime import datetime, timedelta

from ..database.models import Token, ScrapedGap, GapAttempt
from ..analysis.gap_tracking import get_scraping_summary

logger = logging.getLogger(__name__)


def gap_status(token_cashtag: str, days: int = 7) -> None:
    """
    Affiche un résumé complet de l'état des gaps pour un token.

    Args:
        token_cashtag: Cashtag du token (ex: $PIPPIN)
        days: Nombre de jours à analyser (défaut: 7)
    """
    try:
        # Récupérer le token
        token = Token.get(Token.cashtag == token_cashtag)

        print("=" * 80)
        print(f"GAP STATUS: {token.cashtag}")
        print("=" * 80)

        # Récupérer tous les gaps
        gaps = list(ScrapedGap.select().where(ScrapedGap.token == token))

        if not gaps:
            print("\n✓ Aucun gap détecté pour ce token")
            return

        # Grouper par statut
        partial_gaps = [g for g in gaps if g.status == 'partial']
        exhausted_gaps = [g for g in gaps if g.status == 'all_methods_exhausted']
        complete_gaps = [g for g in gaps if g.status == 'complete']
        pending_gaps = [g for g in gaps if g.status == 'pending']

        # 1. GAPS PARTIELS (nécessitent attention)
        if partial_gaps:
            print("\n" + "-" * 80)
            print("GAPS PARTIELS (besoin de re-scraping):")
            print("-" * 80)

            for gap in sorted(partial_gaps, key=lambda g: g.completeness_pct):
                print(f"\nGap #{gap.id}: {gap.since_date} -> {gap.until_date}")
                print(f"  Completude: {gap.completeness_pct:.1f}% ({gap.covered_hours}/{gap.expected_hours}h)")
                print(f"  Methodes essayees: {gap.methods_tried or 'aucune'}")
                print(f"  Derniere tentative: {gap.last_attempt.strftime('%Y-%m-%d %H:%M') if gap.last_attempt else 'jamais'}")

                # Suggérer action
                methods_tried = set(gap.methods_tried.split(',')) if gap.methods_tried else set()
                all_methods = {'twitterio_top', 'twitterio_latest', 'playwright', 'twscrape'}
                methods_remaining = all_methods - methods_tried

                if methods_remaining:
                    print(f"  Suggestion: Essayer {', '.join(methods_remaining)}")
                else:
                    print(f"  Warning: Toutes les methodes essayees")

        # 2. GAPS ÉPUISÉS
        if exhausted_gaps:
            print("\n" + "-" * 80)
            print("GAPS EPUISES (toutes methodes essayees):")
            print("-" * 80)

            for gap in exhausted_gaps[:10]:  # Limiter à 10
                print(f"\nGap #{gap.id}: {gap.since_date} -> {gap.until_date}")
                print(f"  Completude: {gap.completeness_pct:.1f}%")
                print(f"  Methodes: {gap.methods_tried}")
                print(f"  Warning: Probablement vide (pas de donnees disponibles)")

            if len(exhausted_gaps) > 10:
                print(f"\n... et {len(exhausted_gaps) - 10} autres gaps epuises")

        # 3. GAPS PENDING
        if pending_gaps:
            print("\n" + "-" * 80)
            print(f"GAPS EN ATTENTE: {len(pending_gaps)} gaps")
            print("-" * 80)

        # 4. GAPS COMPLETS
        print("\n" + "-" * 80)
        print(f"GAPS COMPLETS: {len(complete_gaps)} gaps (>=95% completude)")
        print("-" * 80)

        # 5. RÉSUMÉ GÉNÉRAL
        print("\n" + "=" * 80)
        print("RESUME")
        print("=" * 80)

        print(f"\nTotal gaps: {len(gaps)}")
        print(f"  - Complets: {len(complete_gaps)} ({len(complete_gaps)/len(gaps)*100:.1f}%)")
        print(f"  - Partiels: {len(partial_gaps)} ({len(partial_gaps)/len(gaps)*100:.1f}%)")
        print(f"  - Epuises: {len(exhausted_gaps)} ({len(exhausted_gaps)/len(gaps)*100:.1f}%)")
        print(f"  - En attente: {len(pending_gaps)} ({len(pending_gaps)/len(gaps)*100:.1f}%)")

        # Complétude moyenne
        avg_completeness = sum(g.completeness_pct for g in gaps) / len(gaps)
        print(f"\nCompletude moyenne: {avg_completeness:.1f}%")

        # Statistiques de scraping
        summary = get_scraping_summary(token, days=days)
        if summary['total_attempts'] > 0:
            print(f"\nTentatives de scraping ({days} derniers jours): {summary['total_attempts']}")

            for method, stats in summary['methods'].items():
                print(f"  - {method}: {stats['success']}/{stats['total_attempts']} reussies ({stats['success_rate']:.1f}%)")

        print("\n" + "=" * 80)

    except Token.DoesNotExist:
        print(f"Erreur: Token {token_cashtag} non trouve")
    except Exception as e:
        print(f"Erreur: {e}")
        import traceback
        traceback.print_exc()


def gap_list(token_cashtag: str, status_filter: str | None = None, limit: int = 20) -> None:
    """
    Liste les gaps d'un token avec détails.

    Args:
        token_cashtag: Cashtag du token (ex: $PIPPIN)
        status_filter: Filtrer par statut ('partial', 'complete', 'exhausted', 'pending')
        limit: Nombre max de gaps à afficher (défaut: 20)
    """
    try:
        token = Token.get(Token.cashtag == token_cashtag)

        print("=" * 80)
        print(f"LISTE DES GAPS: {token.cashtag}")
        if status_filter:
            print(f"Filtre: {status_filter}")
        print("=" * 80)

        # Construire la requête
        query = ScrapedGap.select().where(ScrapedGap.token == token)

        if status_filter:
            query = query.where(ScrapedGap.status == status_filter)

        gaps = list(query.order_by(ScrapedGap.since_date.desc()).limit(limit))

        if not gaps:
            print("\nAucun gap trouve")
            return

        print(f"\n{len(gaps)} gap(s) trouve(s):\n")
        print(f"{'ID':>5} {'Periode':<35} {'Completude':>11} {'Statut':<20} {'Methodes':<30}")
        print("-" * 120)

        for gap in gaps:
            period = f"{gap.since_date[:16]} -> {gap.until_date[:16]}"
            completeness = f"{gap.completeness_pct:>5.1f}%"
            status = gap.status
            methods = gap.methods_tried or '-'

            print(f"{gap.id:>5} {period:<35} {completeness:>11} {status:<20} {methods:<30}")

        if len(gaps) == limit:
            print(f"\n(Limite de {limit} gaps atteinte, utilisez --limit pour en voir plus)")

    except Token.DoesNotExist:
        print(f"Erreur: Token {token_cashtag} non trouve")
    except Exception as e:
        print(f"Erreur: {e}")


def gap_retry(
    token_cashtag: str,
    gap_id: int,
    method: str | None = None,
    dry_run: bool = False
) -> None:
    """
    Réessaye manuellement le scraping d'un gap.

    Args:
        token_cashtag: Cashtag du token (ex: $PIPPIN)
        gap_id: ID du gap à réessayer
        method: Méthode à utiliser ('auto', 'twitterio_top', 'playwright', etc.)
                Si 'auto', essaye les méthodes pas encore tentées
        dry_run: Si True, simule sans lancer le scraping
    """
    try:
        token = Token.get(Token.cashtag == token_cashtag)
        gap = ScrapedGap.get(
            (ScrapedGap.id == gap_id) &
            (ScrapedGap.token == token)
        )

        print("=" * 80)
        print(f"RETRY GAP #{gap_id}: {token.cashtag}")
        print("=" * 80)

        print(f"\nGap: {gap.since_date} -> {gap.until_date}")
        print(f"Completude actuelle: {gap.completeness_pct:.1f}%")
        print(f"Statut: {gap.status}")
        print(f"Methodes deja essayees: {gap.methods_tried or 'aucune'}")

        # Déterminer la méthode à utiliser
        if method is None or method == 'auto':
            # Méthodes pas encore essayées
            methods_tried = set(gap.methods_tried.split(',')) if gap.methods_tried else set()
            all_methods = ['twitterio_top', 'twitterio_latest', 'playwright', 'twscrape']
            methods_remaining = [m for m in all_methods if m not in methods_tried]

            if not methods_remaining:
                print("\nToutes les methodes ont deja ete essayees!")
                print("Specifiez une methode explicite pour forcer un retry.")
                return

            selected_method = methods_remaining[0]
            print(f"\nMethode auto-selectionnee: {selected_method}")
        else:
            selected_method = method
            print(f"\nMethode specifiee: {selected_method}")

        if dry_run:
            print("\n[DRY-RUN] Scraping simule, aucune action effectuee")
            return

        # Lancer le scraping
        print(f"\nLancement du scraping avec {selected_method}...")

        # TODO: Appeler la fonction de scraping appropriée
        # Cela nécessite d'importer et d'appeler le scraper correspondant
        # Pour l'instant, on affiche juste un message

        print("\nWARNING: Cette fonctionnalite necessite l'integration complete avec les scrapers")
        print("Pour l'instant, utilisez manuellement le scraper avec ces parametres:")
        print(f"  Token: {token.cashtag}")
        print(f"  Since: {gap.since_date}")
        print(f"  Until: {gap.until_date}")
        print(f"  Method: {selected_method}")

    except Token.DoesNotExist:
        print(f"Erreur: Token {token_cashtag} non trouve")
    except ScrapedGap.DoesNotExist:
        print(f"Erreur: Gap #{gap_id} non trouve pour {token_cashtag}")
    except Exception as e:
        print(f"Erreur: {e}")


def gap_reset(
    token_cashtag: str,
    gap_id: int | None = None,
    confirm: bool = False
) -> None:
    """
    Réinitialise l'historique des tentatives d'un gap.

    Args:
        token_cashtag: Cashtag du token (ex: $PIPPIN)
        gap_id: ID du gap à réinitialiser (None = tous les gaps du token)
        confirm: Si False, demande confirmation
    """
    try:
        token = Token.get(Token.cashtag == token_cashtag)

        print("=" * 80)
        print(f"RESET GAP HISTORY: {token.cashtag}")
        print("=" * 80)

        if gap_id:
            # Reset un gap spécifique
            gap = ScrapedGap.get(
                (ScrapedGap.id == gap_id) &
                (ScrapedGap.token == token)
            )

            print(f"\nGap #{gap_id}: {gap.since_date} -> {gap.until_date}")
            print(f"Methodes essayees: {gap.methods_tried or 'aucune'}")

            # Compter les tentatives
            attempts_count = GapAttempt.select().where(
                (GapAttempt.token == token) &
                (GapAttempt.since_date == gap.since_date) &
                (GapAttempt.until_date == gap.until_date)
            ).count()

            print(f"Tentatives enregistrees: {attempts_count}")

            if not confirm:
                response = input("\nConfirmer la reinitialisation de ce gap? (OUI/non): ")
                if response != 'OUI':
                    print("Operation annulee")
                    return

            # Supprimer les tentatives
            GapAttempt.delete().where(
                (GapAttempt.token == token) &
                (GapAttempt.since_date == gap.since_date) &
                (GapAttempt.until_date == gap.until_date)
            ).execute()

            # Réinitialiser le gap
            gap.methods_tried = ''
            gap.status = 'pending'
            gap.first_attempt = None
            gap.last_attempt = None
            gap.save()

            print(f"\nGap #{gap_id} reinitialise avec succes")

        else:
            # Reset tous les gaps du token
            gaps_count = ScrapedGap.select().where(ScrapedGap.token == token).count()
            attempts_count = GapAttempt.select().where(GapAttempt.token == token).count()

            print(f"\nGaps a reinitialiser: {gaps_count}")
            print(f"Tentatives a supprimer: {attempts_count}")
            print("\nWARNING: Cette action va supprimer TOUT l'historique de scraping!")

            if not confirm:
                response = input("\nConfirmer la reinitialisation COMPLETE? (tapez 'OUI' pour confirmer): ")
                if response != 'OUI':
                    print("Operation annulee")
                    return

            # Supprimer toutes les tentatives
            GapAttempt.delete().where(GapAttempt.token == token).execute()

            # Réinitialiser tous les gaps
            ScrapedGap.update(
                methods_tried='',
                status='pending',
                first_attempt=None,
                last_attempt=None
            ).where(ScrapedGap.token == token).execute()

            print(f"\nTous les gaps de {token.cashtag} ont ete reinitialises")

    except Token.DoesNotExist:
        print(f"Erreur: Token {token_cashtag} non trouve")
    except ScrapedGap.DoesNotExist:
        print(f"Erreur: Gap #{gap_id} non trouve pour {token_cashtag}")
    except Exception as e:
        print(f"Erreur: {e}")


# Point d'entrée CLI (peut être appelé depuis un script)
if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python gap_commands.py status $TOKEN [days]")
        print("  python gap_commands.py list $TOKEN [status_filter] [limit]")
        print("  python gap_commands.py retry $TOKEN <gap_id> [method]")
        print("  python gap_commands.py reset $TOKEN [gap_id]")
        sys.exit(1)

    command = sys.argv[1]

    if command == 'status':
        token_cashtag = sys.argv[2]
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 7
        gap_status(token_cashtag, days=days)

    elif command == 'list':
        token_cashtag = sys.argv[2]
        status_filter = sys.argv[3] if len(sys.argv) > 3 else None
        limit = int(sys.argv[4]) if len(sys.argv) > 4 else 20
        gap_list(token_cashtag, status_filter=status_filter, limit=limit)

    elif command == 'retry':
        token_cashtag = sys.argv[2]
        gap_id = int(sys.argv[3])
        method = sys.argv[4] if len(sys.argv) > 4 else 'auto'
        gap_retry(token_cashtag, gap_id, method=method)

    elif command == 'reset':
        token_cashtag = sys.argv[2]
        gap_id = int(sys.argv[3]) if len(sys.argv) > 3 else None
        gap_reset(token_cashtag, gap_id=gap_id)

    else:
        print(f"Commande inconnue: {command}")
        sys.exit(1)
