# Commandes Archivées

Ce dossier contient des commandes Discord mises de côté temporairement pour alléger le code.

## Commandes Disponibles

### AdminCommandsArchived
- `/screen` - Lance un Screen des Tokens par MarketCap
- `/filter` - Filtre les tokens par critères

**Fichier:** `admin_commands_archived.py`

### VerificationCommandsArchived
- `/check_verification` - Vérifie le statut de vérification Twitter

**Fichier:** `verification_commands_archived.py`

## Réactivation

Pour réactiver une commande archivée:

1. Dans `discord_bot.py`, décommenter l'import:
   ```python
   from .commands.archived.admin_commands_archived import AdminCommandsArchived
   ```

2. Ajouter le Cog dans `load_cogs()`:
   ```python
   cogs = [
       AdminCommandsArchived(bot, _scheduler),
       ...
   ]
   ```

3. Redémarrer le bot et synchroniser les commandes.

## Notes

- Les commandes archivées conservent toute leur fonctionnalité
- Elles peuvent être réactivées à tout moment
- Les imports utilisent 4 points (`....`) car elles sont dans un sous-dossier
