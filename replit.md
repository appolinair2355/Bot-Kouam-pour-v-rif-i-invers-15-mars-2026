# Baccarat AI Bot

A Telegram bot that analyzes Baccarat game results from the 1xBet API and generates predictions based on suit pattern tracking.

## Architecture

- **Language:** Python 3.12
- **Main file:** `main.py` — bot logic, counters, predictions, Telegram commands
- **Config:** `config.py` — environment-based configuration
- **API utils:** `utils.py` — fetches and parses Baccarat game results from 1xBet API

## Data Source

Game results are fetched directly from the **1xBet live feed API** via periodic polling (every 10 seconds). The API returns player and banker card data for each finished Baccarat game. There is no Telegram source channel — all game data comes from the API.

## How It Works

1. `api_polling_task()` polls the 1xBet API every 10 seconds
2. New finished games are converted to synthetic messages: `✅ #N{game} ({player_suits}) ({banker_suits})`
3. The existing counter logic (`process_game_result`) processes each game
4. **Compteur1**: tracks consecutive suit appearances (player group)
5. **Compteur2**: tracks consecutive suit absences (player group)
6. **Compteur3**: tracks consecutive suit absences (banker group)
7. When thresholds are reached, predictions are sent to the Telegram prediction channel

## Required Secrets

Set these in the Replit Secrets panel:

| Variable | Description |
|----------|-------------|
| `API_ID` | Telegram API ID (from my.telegram.org) |
| `API_HASH` | Telegram API Hash (from my.telegram.org) |
| `BOT_TOKEN` | Telegram Bot Token (from @BotFather) |
| `ADMIN_ID` | Admin Telegram user ID |

## Running

```bash
python main.py
```

The bot starts a web server on port 5000 (`/` and `/health` endpoints) alongside the Telegram bot.

## Prediction Verification Flow (Dynamic)

1. **Partie en cours** (is_finished=False) : `check_prediction_live()` vérifie UNIQUEMENT les costumes du Joueur.
   - Costume prédit trouvé → GAGNÉ immédiatement (message édité).
   - Costume non trouvé → on attend la fin de partie sans pénalité.
2. **Partie terminée** (is_finished=True) : `check_prediction_result()` effectue la vérification finale.
   - Trouvé → GAGNÉ (R0 si cible directe).
   - Non trouvé → passage au numéro suivant (R1, R2, R3).
   - R3 échoué → PERDU.
3. Si le live check a déjà résolu la prédiction (costume trouvé avant fin), `check_prediction_result` ne la retraite pas (déjà supprimée de `pending_predictions`).

## Multi-Canal Secondaire

Chaque type de prédiction est redirigé exactement vers le(s) canal/aux configuré(s) :
- `compteur2` → `COMPTEUR2_CHANNEL_ID` + `CANAL_C2_ID`
- `compteur3_seul` → `CANAL_C3_ID`
- `compteur2_c3` → `CANAL_C2C3_ID`

Tous les canaux secondaires reçoivent les mises à jour gagne/perdu (structure `secondary_channels_tracking` liste).

## Bilan & Conseil

`send_bilan_to_all()` envoie le bilan et le conseil dans des blocs `try/except` indépendants afin que l'échec de l'un n'empêche jamais l'envoi de l'autre.

## Déploiement

Archive ZIP de déploiement : `Molouloo.zip` (contient main.py, utils.py, config.py, requirements.txt, render.yaml, replit.md).

## Dependencies

- `telethon` — Telegram client
- `aiohttp` — async web server
- `requests` — HTTP requests for 1xBet API
- `reportlab` — PDF generation for stats reports
- `python-dateutil` — date utilities
