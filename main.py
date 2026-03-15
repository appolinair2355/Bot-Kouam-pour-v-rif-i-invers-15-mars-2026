import os
import asyncio
import re
import logging
import sys
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Tuple
from datetime import datetime, timedelta
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession
from telethon.errors import ChatWriteForbiddenError, UserBannedInChannelError
from aiohttp import web

from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    ALL_SUITS, SUIT_DISPLAY
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

if not API_ID or API_ID == 0: 
    logger.error("API_ID manquant")
    exit(1)
if not API_HASH: 
    logger.error("API_HASH manquant")
    exit(1)
if not BOT_TOKEN: 
    logger.error("BOT_TOKEN manquant")
    exit(1)

# ============================================================================
# VARIABLES GLOBALES
# ============================================================================

pending_predictions: Dict[int, dict] = {}
current_game_number = 0
last_source_game_number = 0
last_prediction_time: Optional[datetime] = None
prediction_channel_ok = False
client = None
suit_block_until: Dict[str, datetime] = {}
waiting_finalization: Dict[int, dict] = {}

# Compteur2 - Gestion des costumes manquants (interne uniquement)
compteur2_trackers: Dict[str, 'Compteur2Tracker'] = {}
compteur2_seuil_B = 3  # Seuil par défaut
compteur2_active = True

# Compteur3 - Gestion des costumes manquants dans le 2ème groupe de parenthèses
compteur3_trackers: Dict[str, 'Compteur3Tracker'] = {}
compteur3_seuil_B2 = 3  # Seuil B2 par défaut
compteur3_active = True
COMPTEUR3_Z = 3  # Valeur Z : offset numéro quand C3 seul atteint B
COMPTEUR3_E = 3  # Valeur E : offset numéro quand C2 seul ou C2+C3 atteint B

# NOUVEAU: Compteur1 - Gestion des costumes présents consécutifs
compteur1_trackers: Dict[str, 'Compteur1Tracker'] = {}
compteur1_history: List[Dict] = []  # Historique des séries ≥3
MIN_CONSECUTIVE_FOR_STATS = 3  # Minimum pour apparaître dans /stats

# Gestion des écarts entre prédictions
MIN_GAP_BETWEEN_PREDICTIONS = 3  # Écart minimum entre 2 prédictions
last_prediction_number_sent = 0  # Dernier numéro de prédiction envoyé

# Historiques pour la commande /history
finalized_messages_history: List[Dict] = []
MAX_HISTORY_SIZE = 50
prediction_history: List[Dict] = []

# File d'attente de prédictions (plusieurs prédictions possibles)
prediction_queue: List[Dict] = []  # File ordonnée des prédictions en attente
PREDICTION_SEND_AHEAD = 2  # Envoyer la prédiction quand canal source est à N-2

# Canaux secondaires pour redirection
COMPTEUR2_CHANNEL_ID = None     # Canal spécifique pour Compteur2

# ============================================================================
# SYSTÈME DE PAUSE PAR CYCLE (CORRIGÉ)
# ============================================================================

# État de la pause
pause_active = False
pause_counter = 0  # Compteur de prédictions (1-4)
pause_cycle_index = 0  # Index dans le cycle (0, 1, 2...)
pause_message_id = None  # ID du message de pause à éditer
pause_end_time = None  # Heure de fin de pause
pause_task = None  # Tâche de mise à jour du message

# Configuration pause
PAUSE_CYCLE = [3, 5, 4]  # Durées en minutes par défaut
PREDICTIONS_BEFORE_PAUSE = 4  # Nombre de prédictions avant pause

# Expressions de reprise (44 expressions : confiance, proverbes, joie, blague, confort, soulagement)
RESUME_EXPRESSIONS = [
    # ── Joie & Célébration ──────────────────────────────────────────────────
    "🎉 Bingo ! Les prédictions reprennent ! Bot créé par Sossou Kouamé",
    "🚀 C'est reparti mon kiki ! Sossou Kouamé vous présente la suite",
    "🎰 La pause est finie, le jeu continue ! By Sossou Kouamé",
    "🎊 Hourra ! La fête continue ! Sossou Kouamé vous accueille",
    "🥳 On est partis ! Les prédictions sont de retour - Sossou Kouamé",
    "🎆 Feux d'artifice de prédictions ! Sossou Kouamé fait la fête",
    "🎈 Ballons lâchés, prédictions lancées ! By Sossou Kouamé",
    "💃 La danse des prédictions recommence ! Sossou Kouamé vous invite",
    "🎵 Tadam ! Les prédictions sont de retour - Sossou Kouamé",
    "🌈 Arc-en-ciel de victoires en vue ! Bot by Sossou Kouamé",
    # ── Confiance & Force ───────────────────────────────────────────────────
    "🔥 De retour en action ! Bot by Sossou Kouamé",
    "⚡ Énergie rechargée à 100% ! Sossou Kouamé au rapport",
    "💪 Fort comme un lion, précis comme une flèche ! Sossou Kouamé revient",
    "🦁 Le roi de la prédiction est de retour ! Sossou Kouamé aux commandes",
    "🎯 Viser juste, toujours viser juste ! Sossou Kouamé reprend le jeu",
    "⚡ Puissance maximale réactivée ! Sossou Kouamé est prêt",
    "🔥 Inarrêtable ! Les prédictions de Sossou Kouamé reprennent",
    "🚀 Décollage confirmé ! Sossou Kouamé aux commandes de la fusée",
    "🎖️ Médaille de la patience décernée ! Reprise Sossou Kouamé",
    "⚔️ À l'attaque ! Le bot de Sossou Kouamé reprend du service",
    # ── Blagues & Légèreté ──────────────────────────────────────────────────
    "😂 J'ai failli m'endormir mais me voilà ! Sossou Kouamé reprend le service",
    "🤣 Même une pause ne peut pas arrêter Sossou Kouamé — les prédictions reviennent !",
    "😄 Le chat a fait sa sieste, il revient avec des prédictions en or ! By Sossou Kouamé",
    "🎭 Fin de l'entracte, le spectacle continue ! By Sossou Kouamé",
    "🎩 Abracadabra ! Les prédictions réapparaissent - By Sossou Kouamé",
    "🃏 La carte gagnante est tirée ! Sossou Kouamé reprend les prédictions",
    # ── Confort & Soulagement ───────────────────────────────────────────────
    "😌 Respirez, les prédictions reprennent ! Sossou Kouamé veille",
    "🌸 Tout va bien, Sossou Kouamé est là pour les prédictions !",
    "🕊️ La paix retrouvée, les prédictions reprennent ! By Sossou Kouamé",
    "🌊 Après la vague, le calme et la victoire ! Prédictions reprises - Sossou Kouamé",
    "🛡️ Protégés par l'IA de Sossou Kouamé, les prédictions reprennent !",
    "🌻 Comme un tournesol vers le soleil, les prédictions repartent ! Sossou Kouamé",
    "🌙 Même la lune se repose avant de briller ! Sossou Kouamé reprend",
    "🌟 Le spectacle continue ! Bot Telegram de Sossou Kouamé",
    "🔮 La boule de cristal est de nouveau claire ! Sossou Kouamé",
    # ── Proverbes touchants ─────────────────────────────────────────────────
    "🌦️ Après la pluie, le beau temps ! Les prédictions sont de retour - Sossou Kouamé",
    "💎 Le diamant ne brille qu'après avoir été taillé — comme nos prédictions ! Sossou Kouamé",
    "🌅 La nuit la plus sombre précède toujours l'aube la plus belle ! Sossou Kouamé aux commandes",
    "🌱 Qui sème avec patience récolte avec abondance ! Prédictions reprises - Sossou Kouamé",
    "🏔️ Ce qui ne te tue pas te rend plus fort ! Sossou Kouamé plus fort que jamais",
    "🎓 La patience est la mère de toutes les vertus — les prédictions reprennent ! Sossou Kouamé",
    "🤝 Si tu veux aller loin, marchons ensemble vers la victoire ! Sossou Kouamé",
    "🌍 Dieu ne dort jamais, et Sossou Kouamé non plus — prédictions en cours !",
    "💫 Et c'est reparti pour un tour ! Sossou Kouamé vous souhaite bonne chance",
    "🍀 La chance sourit aux audacieux — et aux patients ! Reprise par Sossou Kouamé",
]

# ============================================================================
# FONCTION UTILITAIRE - Conversion ID Canal
# ============================================================================

def normalize_channel_id(channel_id) -> int:
    if not channel_id:
        return None
    
    channel_str = str(channel_id)
    
    if channel_str.startswith('-100'):
        return int(channel_str)
    
    if channel_str.startswith('-'):
        return int(channel_str)
    
    return int(f"-100{channel_str}")

async def resolve_channel(entity_id):
    try:
        if not entity_id:
            return None
        
        normalized_id = normalize_channel_id(entity_id)
        entity = await client.get_entity(normalized_id)
        
        if hasattr(entity, 'broadcast') and entity.broadcast:
            logger.info(f"✅ Canal résolu: {entity.title} (ID: {normalized_id})")
            return entity
        
        if hasattr(entity, 'megagroup') and entity.megagroup:
            logger.info(f"✅ Groupe résolu: {entity.title} (ID: {normalized_id})")
            return entity
            
        return entity
        
    except Exception as e:
        logger.error(f"❌ Impossible de résoudre le canal {entity_id}: {e}")
        return None

# ============================================================================
# CLASSES TRACKERS
# ============================================================================

@dataclass
class Compteur2Tracker:
    """Tracker pour le compteur2 (costumes manquants dans 1er groupe)."""
    suit: str
    counter: int = 0
    last_increment_game: int = 0
    streak_start_game: int = 0  # Jeu où la série d'absences a débuté

    def get_display_name(self) -> str:
        names = {
            '♠': '♠️ Pique',
            '♥': '❤️ Cœur',
            '♦': '♦️ Carreau',
            '♣': '♣️ Trèfle'
        }
        return names.get(self.suit, self.suit)

    def increment(self, game_number: int):
        if self.counter == 0:
            self.streak_start_game = game_number
        self.counter += 1
        self.last_increment_game = game_number
        logger.info(f"📊 Compteur2 {self.suit}: {self.counter} (incrémenté au jeu #{game_number})")

    def reset(self, game_number: int):
        if self.counter > 0:
            logger.info(f"🔄 Compteur2 {self.suit}: reset de {self.counter} à 0 (trouvé au jeu #{game_number})")
        self.counter = 0
        self.last_increment_game = 0
        self.streak_start_game = 0

    def check_threshold(self, seuil_B: int) -> bool:
        return self.counter >= seuil_B

@dataclass
class Compteur3Tracker:
    """Tracker pour le compteur3 (costumes manquants dans le 2ème groupe)."""
    suit: str
    counter: int = 0
    last_increment_game: int = 0
    streak_start_game: int = 0  # Jeu où la série d'absences a débuté

    def get_display_name(self) -> str:
        names = {
            '♠': '♠️ Pique',
            '♥': '❤️ Cœur',
            '♦': '♦️ Carreau',
            '♣': '♣️ Trèfle'
        }
        return names.get(self.suit, self.suit)

    def increment(self, game_number: int):
        if self.counter == 0:
            self.streak_start_game = game_number
        self.counter += 1
        self.last_increment_game = game_number
        logger.info(f"📊 Compteur3 {self.suit}: {self.counter} (incrémenté au jeu #{game_number})")

    def reset(self, game_number: int):
        if self.counter > 0:
            logger.info(f"🔄 Compteur3 {self.suit}: reset de {self.counter} à 0 (trouvé au jeu #{game_number})")
        self.counter = 0
        self.last_increment_game = 0
        self.streak_start_game = 0

    def check_threshold(self, seuil_B2: int) -> bool:
        return self.counter >= seuil_B2

# NOUVEAU: Compteur1 Tracker (costumes présents consécutifs)
@dataclass
class Compteur1Tracker:
    """Tracker pour le compteur1 (costumes présents consécutivement)."""
    suit: str
    counter: int = 0
    start_game: int = 0  # Jeu où la série a commencé
    last_game: int = 0   # Dernier jeu où vu
    
    def get_display_name(self) -> str:
        names = {
            '♠': '♠️ Pique',
            '♥': '❤️ Cœur',
            '♦': '♦️ Carreau',
            '♣': '♣️ Trèfle'
        }
        return names.get(self.suit, self.suit)
    
    def increment(self, game_number: int):
        if self.counter == 0:
            self.start_game = game_number
        self.counter += 1
        self.last_game = game_number
        logger.info(f"🎯 Compteur1 {self.suit}: {self.counter} consécutifs (jeu #{game_number})")
    
    def reset(self, game_number: int):
        # Sauvegarder dans l'historique si ≥ 3 avant reset
        if self.counter >= MIN_CONSECUTIVE_FOR_STATS:
            save_compteur1_series(self.suit, self.counter, self.start_game, self.last_game)
        
        if self.counter > 0:
            logger.info(f"🔄 Compteur1 {self.suit}: reset de {self.counter} à 0 (manqué au jeu #{game_number})")
        self.counter = 0
        self.start_game = 0
        self.last_game = 0
    
    def get_status(self) -> str:
        if self.counter == 0:
            return "0"
        return f"{self.counter} (depuis #{self.start_game})"

# ============================================================================
# FONCTIONS COMPTeur1 (NOUVEAU)
# ============================================================================

def save_compteur1_series(suit: str, count: int, start_game: int, end_game: int):
    """Sauvegarde une série de Compteur1 dans l'historique."""
    global compteur1_history
    
    entry = {
        'suit': suit,
        'count': count,
        'start_game': start_game,
        'end_game': end_game,
        'timestamp': datetime.now()
    }
    
    compteur1_history.insert(0, entry)
    
    # Garder seulement les 100 dernières entrées
    if len(compteur1_history) > 100:
        compteur1_history = compteur1_history[:100]
    
    logger.info(f"💾 Série Compteur1 sauvegardée: {suit} {count} fois (jeux #{start_game}-#{end_game})")

def get_compteur1_stats() -> Dict[str, List[Dict]]:
    """Organise l'historique par costume."""
    stats = {'♥': [], '♠': [], '♦': [], '♣': []}
    
    for entry in compteur1_history:
        suit = entry['suit']
        if suit in stats:
            stats[suit].append(entry)
    
    return stats

def get_compteur1_record(suit: str) -> int:
    """Retourne le record (max consécutifs) pour un costume."""
    max_count = 0
    for entry in compteur1_history:
        if entry['suit'] == suit and entry['count'] > max_count:
            max_count = entry['count']
    return max_count

def update_compteur1(game_number: int, first_group: str):
    """Met à jour le Compteur1 basé sur les costumes présents."""
    global compteur1_trackers
    
    suits_in_first = set(get_suits_in_group(first_group))
    
    for suit in ALL_SUITS:
        tracker = compteur1_trackers[suit]
        
        if suit in suits_in_first:
            # Costume présent → incrémenter
            tracker.increment(game_number)
        else:
            # Costume manquant → reset (et sauvegarder si nécessaire)
            tracker.reset(game_number)

# ============================================================================
# FONCTIONS COMPTEUR3
# ============================================================================

def get_suit_inverse(suit: str) -> Optional[str]:
    """Retourne le costume inverse selon la règle : ♣↔♥ et ♦↔♠."""
    inverses = {
        '♣': '♥',
        '♥': '♣',
        '♦': '♠',
        '♠': '♦',
    }
    return inverses.get(suit, None)

def update_compteur3(game_number: int, second_group: str):
    """Met à jour Compteur3 avec le 2ème groupe de parenthèses."""
    global compteur3_trackers

    if not second_group:
        return

    suits_in_second = set(get_suits_in_group(second_group))

    for suit in ALL_SUITS:
        tracker = compteur3_trackers[suit]
        if suit in suits_in_second:
            tracker.reset(game_number)
        else:
            tracker.increment(game_number)

# ============================================================================
# FONCTIONS D'HISTORIQUE
# ============================================================================

def add_to_history(game_number: int, message_text: str, first_group: str, suits_found: List[str]):
    global finalized_messages_history
    
    entry = {
        'timestamp': datetime.now(),
        'game_number': game_number,
        'message_text': message_text[:200],
        'first_group': first_group,
        'suits_found': suits_found,
        'predictions_verified': []
    }
    
    finalized_messages_history.insert(0, entry)
    
    if len(finalized_messages_history) > MAX_HISTORY_SIZE:
        finalized_messages_history = finalized_messages_history[:MAX_HISTORY_SIZE]

def add_prediction_to_history(game_number: int, suit: str, verification_games: List[int], prediction_type: str = 'standard', reason_text: str = ''):
    global prediction_history
    
    prediction_history.insert(0, {
        'predicted_game': game_number,
        'suit': suit,
        'predicted_at': datetime.now(),
        'verification_games': verification_games,
        'status': 'en_cours',
        'verified_at': None,
        'verified_by_game': None,
        'rattrapage_level': 0,
        'verified_by': [],
        'type': prediction_type,
        'reason_text': reason_text
    })
    
    if len(prediction_history) > MAX_HISTORY_SIZE:
        prediction_history = prediction_history[:MAX_HISTORY_SIZE]

def update_prediction_in_history(game_number: int, suit: str, verified_by_game: int, 
                                verified_by_group: str, rattrapage_level: int, final_status: str):
    global finalized_messages_history, prediction_history
    
    for pred in prediction_history:
        if pred['predicted_game'] == game_number and pred['suit'] == suit:
            pred['verified_by'].append({
                'game_number': verified_by_game,
                'first_group': verified_by_group,
                'rattrapage_level': rattrapage_level
            })
            pred['status'] = final_status
            pred['verified_at'] = datetime.now()
            pred['verified_by_game'] = verified_by_game
            pred['rattrapage_level'] = rattrapage_level
            break
    
    for msg in finalized_messages_history:
        if msg['game_number'] == verified_by_game:
            msg['predictions_verified'].append({
                'predicted_game': game_number,
                'suit': suit,
                'rattrapage_level': rattrapage_level
            })
            break

# ============================================================================
# INITIALISATION
# ============================================================================

def initialize_trackers():
    """Initialise les trackers Compteur1, Compteur2 et Compteur3."""
    global compteur2_trackers, compteur1_trackers, compteur3_trackers

    for suit in ALL_SUITS:
        compteur2_trackers[suit] = Compteur2Tracker(suit=suit)
        compteur1_trackers[suit] = Compteur1Tracker(suit=suit)
        compteur3_trackers[suit] = Compteur3Tracker(suit=suit)
        logger.info(f"📊 Trackers {suit}: Compteur1, Compteur2 & Compteur3 initialisés")

def is_message_finalized(message: str) -> bool:
    if '⏰' in message:
        return False
    return '✅' in message or '🔰' in message

def is_message_being_edited(message: str) -> bool:
    return '⏰' in message

def extract_parentheses_groups(message: str) -> List[str]:
    scored_groups = re.findall(r"(\d+)?\(([^)]*)\)", message)
    if scored_groups:
        return [f"{score}:{content}" if score else content for score, content in scored_groups]
    return re.findall(r"\(([^)]*)\)", message)

def get_suits_in_group(group_str: str) -> List[str]:
    if ':' in group_str:
        group_str = group_str.split(':', 1)[1]
    
    normalized = group_str
    for old, new in [('❤️', '♥'), ('❤', '♥'), ('♥️', '♥'),
                     ('♠️', '♠'), ('♦️', '♦'), ('♣️', '♣')]:
        normalized = normalized.replace(old, new)
    
    return [suit for suit in ALL_SUITS if suit in normalized]

def block_suit(suit: str, minutes: int = 5):
    suit_block_until[suit] = datetime.now() + timedelta(minutes=minutes)
    logger.info(f"🔒 {suit} bloqué {minutes}min")

# ============================================================================
# SYSTÈME DE PAUSE - GESTION (CORRIGÉ)
# ============================================================================

def format_pause_message(duration_min: int, remaining_seconds: int) -> str:
    """Formate le message de pause avec temps dynamique."""
    if remaining_seconds <= 0:
        return f"""⏸️ PAUSE TERMINÉE

✅ Fin de la pause
🔄 Préparation de la reprise...

🤖 Baccarat AI"""
    
    minutes = remaining_seconds // 60
    seconds = remaining_seconds % 60
    
    return f"""⏸️ PAUSE ACTIVE

🕐 Durée: {duration_min} minutes
⏳ Temps restant: {minutes}:{seconds:02d}

🤖 Baccarat AI"""

def format_resume_message() -> str:
    """Choisit une expression aléatoire de reprise."""
    return random.choice(RESUME_EXPRESSIONS)

async def update_pause_message(duration_min: int, remaining_seconds: int):
    """Met à jour le message de pause en temps réel."""
    global pause_message_id, pause_active, pause_end_time
    
    if not pause_active or not pause_message_id:
        return
    
    try:
        prediction_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
        if not prediction_entity:
            return
        
        display_seconds = max(0, remaining_seconds)
        msg = format_pause_message(duration_min, display_seconds)
        
        await client.edit_message(prediction_entity, pause_message_id, msg, parse_mode='markdown')
        
    except Exception as e:
        logger.error(f"❌ Erreur mise à jour pause: {e}")

async def pause_countdown_task(duration_min: int):
    """Tâche qui met à jour le message de pause chaque seconde."""
    global pause_active, pause_message_id, pause_end_time
    
    total_seconds = duration_min * 60
    
    for i in range(total_seconds, 0, -1):
        if not pause_active:
            logger.info("⏸️ Pause annulée manuellement")
            return
        
        await update_pause_message(duration_min, i)
        await asyncio.sleep(1)
    
    if pause_active:
        logger.info("⏸️ Temps écoulé, fin de pause automatique")
        await end_pause()

async def start_pause():
    """Démarre une pause (appelé manuellement via /pause on ou par fin de cycle)."""
    global pause_active, pause_counter, pause_cycle_index, pause_message_id, pause_end_time, pause_task
    
    if pause_active:
        logger.warning("⏸️ Pause déjà active")
        return
    
    if pending_predictions:
        logger.warning(f"⏸️ start_pause: {len(pending_predictions)} prédiction(s) encore active(s), pause reportée")
        return
    
    duration = PAUSE_CYCLE[pause_cycle_index % len(PAUSE_CYCLE)]
    
    pause_active = True
    pause_end_time = datetime.now() + timedelta(minutes=duration)
    
    try:
        prediction_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
        if prediction_entity:
            msg = format_pause_message(duration, duration * 60)
            sent = await client.send_message(prediction_entity, msg, parse_mode='markdown')
            pause_message_id = sent.id
            
            pause_task = asyncio.create_task(pause_countdown_task(duration))
            
            logger.info(f"⏸️ PAUSE DÉMARRÉE: {duration} min (cycle index: {pause_cycle_index})")
    except Exception as e:
        logger.error(f"❌ Erreur démarrage pause: {e}")
        pause_active = False

async def end_pause():
    """Termine la pause et envoie message de reprise."""
    global pause_active, pause_counter, pause_cycle_index, pause_message_id, pause_end_time, pause_task
    
    if not pause_active:
        return
    
    pause_active = False
    pause_counter = 0
    pause_cycle_index += 1
    pause_end_time = None
    
    if pause_task and not pause_task.done():
        pause_task.cancel()
        try:
            await pause_task
        except asyncio.CancelledError:
            pass
    
    try:
        prediction_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
        if prediction_entity and pause_message_id:
            resume_msg = format_resume_message()
            
            await client.edit_message(
                prediction_entity, 
                pause_message_id, 
                f"✅ **PAUSE TERMINÉE**\n\n{resume_msg}", 
                parse_mode='markdown'
            )
            
            logger.info(f"▶️ PAUSE TERMINÉE - Reprise avec: {resume_msg[:50]}...")
            
            pause_message_id = None
            
            if prediction_queue:
                logger.info(f"📤 {len(prediction_queue)} prédictions en attente, traitement...")
                await process_prediction_queue(current_game_number)
                
    except Exception as e:
        logger.error(f"❌ Erreur fin pause: {e}")

def increment_pause_counter():
    """Incrémente le compteur de pause et vérifie si pause nécessaire."""
    global pause_counter, pause_active
    
    if pause_active:
        return False
    
    pause_counter += 1
    logger.info(f"⏸️ Compteur pause: {pause_counter}/{PREDICTIONS_BEFORE_PAUSE}")
    
    if pause_counter >= PREDICTIONS_BEFORE_PAUSE:
        logger.info("⏸️ Seuil atteint, pause programmée après vérification")
        return True
    
    return False

async def check_and_trigger_pause(game_number: int):
    """Vérifie si une prédiction terminée doit déclencher la pause.
    La pause ne démarre que lorsque toutes les prédictions en cours sont vérifiées."""
    global pause_counter, pause_active
    
    if pause_active:
        return
    
    if pause_counter >= PREDICTIONS_BEFORE_PAUSE:
        # Attendre que TOUTES les prédictions en cours soient terminées
        if pending_predictions:
            remaining = list(pending_predictions.keys())
            logger.info(f"⏸️ Pause en attente — prédictions encore actives: {remaining}")
            return
        
        await start_pause()

# ============================================================================
# GESTION DES PRÉDICTIONS - MESSAGES SIMPLIFIÉS
# ============================================================================

def format_prediction_message(game_number: int, suit: str, status: str = 'en_cours', 
                             current_check: int = None, verified_games: List[int] = None,
                             rattrapage: int = 0) -> str:
    suit_display = SUIT_DISPLAY.get(suit, suit)
    
    if status == 'en_cours':
        verif_parts = []
        
        for i in range(3):
            check_num = game_number + i
            
            if current_check == check_num:
                verif_parts.append(f"🔵#{check_num}")
            elif verified_games and check_num in verified_games:
                continue
            else:
                verif_parts.append(f"⬜#{check_num}")
        
        verif_line = " | ".join(verif_parts)
        
        return f"""🎰 PRÉDICTION #{game_number}
🎯 Couleur: {suit_display}
📊 Statut: En cours ⏳
🔍 Vérification: {verif_line}"""
    
    elif status == 'gagne':
        if rattrapage == 0:
            status_text = "✅0️⃣GAGNÉ DIRECT 🎉"
        else:
            status_text = f"✅{rattrapage}️⃣GAGNÉ R{rattrapage} 🎉"
        
        return f"""🏆 **PRÉDICTION #{game_number}**

🎯 **Couleur:** {suit_display}
✅ **Statut:** {status_text}"""
    
    elif status == 'perdu':
        return f"""💔 **PRÉDICTION #{game_number}**

🎯 **Couleur:** {suit_display}
❌ **Statut:** PERDU 😭"""
    
    return ""

# ============================================================================
# ENVOI MULTI-CANAUX
# ============================================================================

async def send_prediction_to_channel(channel_id: int, game_number: int, suit: str, 
                                    prediction_type: str, is_secondary: bool = False) -> Optional[int]:
    try:
        if not is_secondary and suit in suit_block_until and datetime.now() < suit_block_until[suit]:
            logger.info(f"🔒 {suit} bloqué, prédiction annulée")
            return None
        
        if not channel_id:
            return None
        
        channel_entity = await resolve_channel(channel_id)
        if not channel_entity:
            logger.error(f"❌ Canal {channel_id} inaccessible")
            return None
        
        msg = format_prediction_message(game_number, suit, 'en_cours', game_number, [])
        
        sent = await client.send_message(channel_entity, msg, parse_mode='markdown')
        logger.info(f"✅ Envoyé à {'canal secondaire' if is_secondary else 'canal principal'} {channel_id}: #{game_number} {suit}")
        return sent.id
        
    except ChatWriteForbiddenError:
        logger.error(f"❌ Pas de permission dans {channel_id}")
        return None
    except UserBannedInChannelError:
        logger.error(f"❌ Bot banni de {channel_id}")
        return None
    except Exception as e:
        logger.error(f"❌ Erreur envoi à {channel_id}: {e}")
        return None

async def send_prediction_multi_channel(game_number: int, suit: str, prediction_type: str = 'standard', reason_text: str = '') -> bool:
    """Envoie la prédiction au canal principal ET aux canaux secondaires selon le type."""
    global last_prediction_time, last_prediction_number_sent, COMPTEUR2_CHANNEL_ID
    
    success = False
    
    if PREDICTION_CHANNEL_ID:
        # ── VERROU SYNCHRONE ─────────────────────────────────────────────────
        # Réserver la place dans pending_predictions AVANT tout await.
        # Si une autre tâche asyncio tourne pendant les awaits ci-dessous,
        # elle verra pending_predictions non vide et ne lancera pas de 2e prédiction.
        if game_number in pending_predictions:
            logger.warning(f"⚠️ #{game_number} déjà réservé dans pending, envoi annulé")
            return False
        
        old_last = last_prediction_number_sent
        last_prediction_number_sent = game_number  # gap check immédiatement effectif
        
        pending_predictions[game_number] = {
            'suit': suit,
            'message_id': None,        # sera mis à jour après l'envoi Telegram
            'status': 'sending',       # placeholder — bloque les vérifications concurrentes
            'type': prediction_type,
            'sent_time': datetime.now(),
            'verification_games': [game_number, game_number + 1, game_number + 2],
            'verified_games': [],
            'found_at': None,
            'rattrapage': 0,
            'current_check': game_number
        }
        # ── FIN VERROU SYNCHRONE ─────────────────────────────────────────────
        
        msg_id = await send_prediction_to_channel(
            PREDICTION_CHANNEL_ID, game_number, suit, prediction_type, is_secondary=False
        )
        
        if msg_id:
            last_prediction_time = datetime.now()
            pending_predictions[game_number]['message_id'] = msg_id
            pending_predictions[game_number]['status'] = 'en_cours'
            add_prediction_to_history(game_number, suit, [game_number, game_number + 1, game_number + 2], prediction_type, reason_text)
            success = True
            
            # Envoyer aux canaux secondaires SEULEMENT si le canal principal a réussi
            # et stocker le message ID pour pouvoir mettre à jour le résultat plus tard
            secondary_channel_id = None
            if prediction_type == 'compteur2' and COMPTEUR2_CHANNEL_ID:
                secondary_channel_id = COMPTEUR2_CHANNEL_ID
            
            if secondary_channel_id:
                sec_msg_id = await send_prediction_to_channel(
                    secondary_channel_id, game_number, suit, prediction_type, is_secondary=True
                )
                if sec_msg_id:
                    pending_predictions[game_number]['secondary_message_id'] = sec_msg_id
                    pending_predictions[game_number]['secondary_channel_id'] = secondary_channel_id
                    logger.info(f"📡 Canal secondaire {secondary_channel_id}: #{game_number} envoyé (msg {sec_msg_id})")
        else:
            # Envoi échoué — retirer le placeholder pour ne pas bloquer le système
            if game_number in pending_predictions and pending_predictions[game_number]['status'] == 'sending':
                del pending_predictions[game_number]
            last_prediction_number_sent = old_last  # restaurer l'ancien last
    
    if success and not pause_active:
        need_pause = increment_pause_counter()
        if need_pause:
            logger.info(f"⏸️ La {PREDICTIONS_BEFORE_PAUSE}ème prédiction (#{game_number}) va déclencher la pause après vérification")
    
    return success

async def update_prediction_message(game_number: int, status: str, rattrapage: int = 0):
    """Met à jour le statut d'une prédiction (uniquement canal principal)."""
    global pause_active, pause_counter, pause_cycle_index, pause_message_id, pause_end_time, pause_task
    
    if game_number not in pending_predictions:
        logger.warning(f"⚠️ update_prediction_message: #{game_number} introuvable (déjà traité?)")
        return
    
    pred = pending_predictions[game_number]
    suit = pred['suit']
    msg_id = pred['message_id']
    new_msg = format_prediction_message(game_number, suit, status, rattrapage=rattrapage)
    
    if 'gagne' in status:
        logger.info(f"✅ Gagné: #{game_number} (R{rattrapage})")
    else:
        logger.info(f"❌ Perdu: #{game_number}")
        block_suit(suit, 5)
    
    # ── SECTION SYNCHRONE (aucun await) ─────────────────────────────────────
    # Tout ce qui suit se fait AVANT le premier await.
    # Cela garantit qu'aucune tâche concurrente ne peut s'intercaler.
    
    del pending_predictions[game_number]
    
    # Si les conditions de pause sont atteintes, verrouiller pause_active = True
    # IMMÉDIATEMENT — avant tout await — pour bloquer les envois concurrents.
    pause_to_start = False
    pause_duration = None
    if not pause_active and pause_counter >= PREDICTIONS_BEFORE_PAUSE and not pending_predictions:
        pause_to_start = True
        pause_duration = PAUSE_CYCLE[pause_cycle_index % len(PAUSE_CYCLE)]
        pause_active = True  # ← VERROU INSTANTANÉ
        pause_end_time = datetime.now() + timedelta(minutes=pause_duration)
        logger.info(f"⏸️ Pause verrouillée ({pause_duration} min) — aucun envoi possible")
    # ── FIN SECTION SYNCHRONE ────────────────────────────────────────────────
    
    # Éditer le message de prédiction — canal principal
    try:
        prediction_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
        if prediction_entity and msg_id:
            await client.edit_message(prediction_entity, msg_id, new_msg, parse_mode='markdown')
        elif not prediction_entity:
            logger.error("❌ Canal principal inaccessible pour mise à jour")
    except Exception as e:
        logger.error(f"❌ Erreur édition message #{game_number}: {e}")
    
    # Éditer le message de prédiction — canal secondaire (même contenu)
    sec_msg_id = pred.get('secondary_message_id')
    sec_channel_id = pred.get('secondary_channel_id')
    if sec_msg_id and sec_channel_id:
        try:
            sec_entity = await resolve_channel(sec_channel_id)
            if sec_entity:
                await client.edit_message(sec_entity, sec_msg_id, new_msg, parse_mode='markdown')
        except Exception as e:
            logger.error(f"❌ Erreur édition canal secondaire #{game_number}: {e}")
    
    # Finaliser l'envoi du message de pause si nécessaire
    if pause_to_start:
        try:
            prediction_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
            if prediction_entity:
                pause_msg = format_pause_message(pause_duration, pause_duration * 60)
                sent = await client.send_message(prediction_entity, pause_msg, parse_mode='markdown')
                pause_message_id = sent.id
                pause_task = asyncio.create_task(pause_countdown_task(pause_duration))
                logger.info(f"⏸️ PAUSE DÉMARRÉE: {pause_duration} min (cycle index: {pause_cycle_index})")
        except Exception as e:
            logger.error(f"❌ Erreur envoi message pause: {e}")

async def update_prediction_progress(game_number: int, current_check: int):
    """Met à jour l'affichage de la progression (canal principal uniquement)."""
    if game_number not in pending_predictions:
        return
    
    pred = pending_predictions[game_number]
    suit = pred['suit']
    msg_id = pred['message_id']
    verified_games = pred.get('verified_games', [])
    
    pred['current_check'] = current_check
    
    msg = format_prediction_message(game_number, suit, 'en_cours', current_check, verified_games)
    
    # Canal principal
    try:
        prediction_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
        if prediction_entity:
            await client.edit_message(prediction_entity, msg_id, msg, parse_mode='markdown')
    except Exception as e:
        logger.error(f"❌ Erreur update progress: {e}")
    
    # Canal secondaire (synchronisation progression)
    sec_msg_id = pred.get('secondary_message_id')
    sec_channel_id = pred.get('secondary_channel_id')
    if sec_msg_id and sec_channel_id:
        try:
            sec_entity = await resolve_channel(sec_channel_id)
            if sec_entity:
                await client.edit_message(sec_entity, sec_msg_id, msg, parse_mode='markdown')
        except Exception as e:
            logger.error(f"❌ Erreur update progress canal secondaire: {e}")

async def check_prediction_result(game_number: int, first_group: str) -> bool:
    suits_in_result = get_suits_in_group(first_group)
    
    if game_number in pending_predictions:
        pred = pending_predictions[game_number]
        if pred['status'] != 'en_cours':
            return False
            
        target_suit = pred['suit']
        
        if game_number in pred['verified_games']:
            return False
        
        pred['verified_games'].append(game_number)
        
        logger.info(f"🔍 Vérification #{game_number}: {target_suit} dans {suits_in_result}?")
        
        if target_suit in suits_in_result:
            await update_prediction_message(game_number, 'gagne', 0)
            update_prediction_in_history(game_number, target_suit, game_number, first_group, 0, 'gagne_r0')
            return True
        else:
            pred['rattrapage'] = 1
            next_check = game_number + 1
            logger.info(f"❌ #{game_number} non trouvé, attente #{next_check}")
            await update_prediction_progress(game_number, next_check)
            return False
    
    for original_game, pred in list(pending_predictions.items()):
        if pred['status'] != 'en_cours':
            continue
            
        target_suit = pred['suit']
        rattrapage = pred.get('rattrapage', 0)
        expected_game = original_game + rattrapage
        
        if game_number == expected_game and rattrapage > 0:
            if game_number in pred['verified_games']:
                return False
            
            pred['verified_games'].append(game_number)
            
            logger.info(f"🔍 Vérification R{rattrapage} #{game_number}: {target_suit} dans {suits_in_result}?")
            
            if target_suit in suits_in_result:
                await update_prediction_message(original_game, 'gagne', rattrapage)
                update_prediction_in_history(original_game, target_suit, game_number, first_group, rattrapage, f'gagne_r{rattrapage}')
                return True
            else:
                if rattrapage < 2:
                    pred['rattrapage'] = rattrapage + 1
                    next_check = original_game + rattrapage + 1
                    logger.info(f"❌ R{rattrapage} échoué, attente #{next_check}")
                    await update_prediction_progress(original_game, next_check)
                    return False
                else:
                    logger.info(f"❌ R2 échoué, prédiction perdue")
                    await update_prediction_message(original_game, 'perdu', 2)
                    update_prediction_in_history(original_game, target_suit, game_number, first_group, 2, 'perdu')
                    return False
    
    return False

# ============================================================================
# GESTION #R ET COMPTEUR2 (MODIFIÉ - avec blocage costumes)
# ============================================================================

def extract_first_two_groups(message: str) -> tuple:
    groups = extract_parentheses_groups(message)
    if len(groups) >= 2:
        return groups[0], groups[1]
    elif len(groups) == 1:
        return groups[0], ""
    return "", ""

def update_compteur2(game_number: int, first_group: str):
    """Met à jour Compteur2."""
    global compteur2_trackers, compteur2_seuil_B
    
    suits_in_first = set(get_suits_in_group(first_group))
    
    for suit in ALL_SUITS:
        tracker = compteur2_trackers[suit]
        
        if suit in suits_in_first:
            tracker.reset(game_number)
        else:
            tracker.increment(game_number)

def get_all_counter_predictions(current_game: int) -> List[tuple]:
    """Logique unifiée de prédiction C2/C3.

    Règles :
      1. C2 seul atteint B        → prédit inverse(C2),   numéro = last_source + E
      2. C2 et C3 inverses à B    → prédit C2 manquant,   numéro = last_source + E
      3. C3 seul atteint B        → prédit inverse(C3),   numéro = last_source + Z
    """
    global compteur2_trackers, compteur2_seuil_B
    global compteur3_trackers, compteur3_seuil_B2, compteur3_active
    global COMPTEUR3_Z, COMPTEUR3_E, last_source_game_number

    ready = []
    base = last_source_game_number if last_source_game_number > 0 else current_game

    # ── BOUCLE 1 : costumes C2 ayant atteint leur seuil ──────────────────
    for suit_c2 in ALL_SUITS:
        tc2 = compteur2_trackers.get(suit_c2)
        if not tc2:
            continue

        eff_B = compteur2_seuil_B
        if tc2.counter < eff_B:
            continue

        c2_display = SUIT_DISPLAY.get(suit_c2, suit_c2)
        streak_start = tc2.streak_start_game
        interval_end = streak_start + eff_B - 1

        inv_c2 = get_suit_inverse(suit_c2)
        tc3_inv = compteur3_trackers.get(inv_c2) if (inv_c2 and compteur3_active) else None
        c3_inv_ready = tc3_inv is not None and tc3_inv.counter >= compteur3_seuil_B2

        if c3_inv_ready and inv_c2:
            # Règle 2 : C2 + C3 inverses → prédit C2 manquant, offset E
            inv_display = SUIT_DISPLAY.get(inv_c2, inv_c2)
            c3_start = tc3_inv.streak_start_game
            c3_interval_end = c3_start + compteur3_seuil_B2 - 1
            pred_number = base + COMPTEUR3_E
            predicted_suit = suit_c2
            pred_type = 'compteur2_c3'
            reason = (
                f"C2 manque {c2_display} [#{streak_start}→#{interval_end}] ({eff_B} absences)\n"
                f"C3 manque {inv_display} [#{c3_start}→#{c3_interval_end}] ({compteur3_seuil_B2} absences)\n"
                f"C2 et C3 inverses confirmés : {c2_display} ↔ {inv_display}\n"
                f"E = {COMPTEUR3_E} → Costume C2 prédit ({c2_display})"
            )
            logger.info(
                f"📊+🔄 C2+C3 inverses: {suit_c2} ↔ {inv_c2} "
                f"→ Prédiction #{pred_number} ({predicted_suit}) [E={COMPTEUR3_E}]"
            )
            tc3_inv.reset(current_game)
        else:
            # Règle 1 : C2 seul → prédit inverse(C2), offset E
            inv_display = SUIT_DISPLAY.get(inv_c2, inv_c2) if inv_c2 else '?'
            pred_number = base + COMPTEUR3_E
            predicted_suit = inv_c2 if inv_c2 else suit_c2
            pred_type = 'compteur2'
            reason = (
                f"C2 manque {c2_display} [#{streak_start}→#{interval_end}] ({eff_B} absences)\n"
                f"C3 sans manque correspondant\n"
                f"E = {COMPTEUR3_E} → Inverse de C2 prédit ({inv_display})"
            )
            logger.info(
                f"📊 C2 seul: {suit_c2} → inverse {inv_c2} "
                f"→ Prédiction #{pred_number} [E={COMPTEUR3_E}]"
            )

        ready.append((predicted_suit, pred_number, pred_type, reason))
        tc2.reset(current_game)

    # ── BOUCLE 2 : costumes C3 ayant atteint leur seuil (non consommés) ──
    if compteur3_active:
        for suit_c3 in ALL_SUITS:
            tc3 = compteur3_trackers.get(suit_c3)
            # Si tc3 a été reset en boucle 1, son counter est 0 → ignoré automatiquement
            if not tc3 or tc3.counter < compteur3_seuil_B2:
                continue

            # Règle 3 : C3 seul → prédit inverse(C3), offset Z
            inv_c3 = get_suit_inverse(suit_c3)
            c3_display = SUIT_DISPLAY.get(suit_c3, suit_c3)
            inv_display = SUIT_DISPLAY.get(inv_c3, inv_c3) if inv_c3 else '?'
            c3_start = tc3.streak_start_game
            c3_interval_end = c3_start + compteur3_seuil_B2 - 1
            pred_number = base + COMPTEUR3_Z
            predicted_suit = inv_c3 if inv_c3 else suit_c3
            pred_type = 'compteur3_seul'
            reason = (
                f"C3 manque {c3_display} [#{c3_start}→#{c3_interval_end}] ({compteur3_seuil_B2} absences)\n"
                f"C2 inverse absent (sous seuil)\n"
                f"Z = {COMPTEUR3_Z} → Inverse de C3 prédit ({inv_display})"
            )
            logger.info(
                f"🔁 C3 seul: {suit_c3} → inverse {inv_c3} "
                f"→ Prédiction #{pred_number} [Z={COMPTEUR3_Z}]"
            )
            tc3.reset(current_game)
            ready.append((predicted_suit, pred_number, pred_type, reason))

    return ready


def get_synchro_status() -> List[dict]:
    """Retourne l'état des paires inverses C2+C3 pour affichage /synchro.
    Ne modifie aucun compteur.
    Paires inverses : ♣↔❤️  et  ♦↔♠️
    Le suivi est toujours basé sur des numéros consécutifs finalisés (🔰/✅).
    """
    global compteur2_trackers, compteur2_seuil_B
    global compteur3_trackers, compteur3_seuil_B2

    pairs = [('♣', '♥'), ('♦', '♠')]
    result = []

    for suit_c2, suit_c3 in pairs:
        tc2 = compteur2_trackers.get(suit_c2)
        tc3 = compteur3_trackers.get(suit_c3)
        if not tc2 or not tc3:
            continue

        effective_B = compteur2_seuil_B

        c2_ready = tc2.counter >= effective_B
        c3_ready = tc3.counter >= compteur3_seuil_B2

        synchro = c2_ready and c3_ready

        result.append({
            'suit_c2': suit_c2,
            'suit_c3': suit_c3,
            'c2_counter': tc2.counter,
            'c2_threshold': effective_B,
            'c2_streak_start': tc2.streak_start_game,
            'c2_last_game': tc2.last_increment_game,
            'c2_ready': c2_ready,
            'c3_counter': tc3.counter,
            'c3_threshold': compteur3_seuil_B2,
            'c3_streak_start': tc3.streak_start_game,
            'c3_last_game': tc3.last_increment_game,
            'c3_ready': c3_ready,
            'synchro': synchro,
        })

    return result




# ============================================================================
# GESTION INTELLIGENTE DE LA FILE D'ATTENTE (avec pause)
# ============================================================================

def can_accept_prediction(pred_number: int) -> bool:
    global prediction_queue, pending_predictions, last_prediction_number_sent, MIN_GAP_BETWEEN_PREDICTIONS, pause_active
    
    if pause_active:
        logger.info(f"⛔ En pause, prédiction #{pred_number} rejetée")
        return False
    
    if last_prediction_number_sent > 0:
        gap = pred_number - last_prediction_number_sent
        if gap < MIN_GAP_BETWEEN_PREDICTIONS:
            logger.info(f"⛔ Écart insuffisant avec dernier envoyé (#{last_prediction_number_sent}): {gap} < {MIN_GAP_BETWEEN_PREDICTIONS}")
            return False
    
    # Vérifier l'écart contre les prédictions actuellement en cours de vérification
    for active_num in pending_predictions:
        gap = abs(pred_number - active_num)
        if gap < MIN_GAP_BETWEEN_PREDICTIONS:
            logger.info(f"⛔ Écart insuffisant avec prédiction active (#{active_num}): {gap} < {MIN_GAP_BETWEEN_PREDICTIONS}")
            return False
    
    for queued_pred in prediction_queue:
        existing_num = queued_pred['game_number']
        gap = abs(pred_number - existing_num)
        if gap < MIN_GAP_BETWEEN_PREDICTIONS:
            logger.info(f"⛔ Écart insuffisant avec file d'attente (#{existing_num}): {gap} < {MIN_GAP_BETWEEN_PREDICTIONS}")
            return False
    
    return True

def add_to_prediction_queue(game_number: int, suit: str, prediction_type: str, reason_text: str = '') -> bool:
    global prediction_queue, pause_active
    
    if pause_active:
        logger.info(f"⏸️ En pause, #{game_number} non ajouté")
        return False
    
    for pred in prediction_queue:
        if pred['game_number'] == game_number:
            logger.info(f"⚠️ Prédiction #{game_number} déjà dans la file")
            return False
    
    if not can_accept_prediction(game_number):
        logger.info(f"❌ Prédiction #{game_number} rejetée - écart insuffisant")
        return False
    
    new_pred = {
        'game_number': game_number,
        'suit': suit,
        'type': prediction_type,
        'reason_text': reason_text,
        'added_at': datetime.now()
    }
    
    prediction_queue.append(new_pred)
    prediction_queue.sort(key=lambda x: x['game_number'])
    
    logger.info(f"📥 Prédiction #{game_number} ({suit}) ajoutée à la file. Total: {len(prediction_queue)}")
    return True

async def process_prediction_queue(current_game: int):
    global prediction_queue, pending_predictions, pause_active
    
    if pause_active:
        return
    
    # RÈGLE 1: Jamais de nouvelle prédiction si une est encore en cours de vérification
    if pending_predictions:
        logger.info(f"⏳ {len(pending_predictions)} prédiction(s) en cours, file en attente")
        return
    
    to_remove = []
    to_send = None
    
    for pred in list(prediction_queue):
        pred_number = pred['game_number']
        suit = pred['suit']
        pred_type = pred['type']
        
        # RÈGLE 2: Prédiction expirée — le moment optimal est passé (moins de PREDICTION_SEND_AHEAD jeux restants)
        if current_game > pred_number - PREDICTION_SEND_AHEAD:
            logger.warning(f"⏰ Prédiction #{pred_number} ({suit}) EXPIRÉE — canal à #{current_game}, trop tard")
            to_remove.append(pred)
            continue
        
        # RÈGLE 3: Envoyer uniquement quand on est exactement au bon moment (N-PREDICTION_SEND_AHEAD)
        if current_game == pred_number - PREDICTION_SEND_AHEAD:
            to_send = pred
            break
    
    # Nettoyer les expirées
    for pred in to_remove:
        prediction_queue.remove(pred)
        logger.info(f"🗑️ #{pred['game_number']} retiré (expiré). Restant: {len(prediction_queue)}")
    
    # Envoyer la prédiction retenue
    if to_send:
        pred_number = to_send['game_number']
        suit = to_send['suit']
        pred_type = to_send['type']
        
        # Vérification finale juste avant envoi (protection race condition)
        if pause_active:
            logger.warning(f"⚠️ Pause détectée avant envoi #{pred_number}, annulé")
            return
        if pending_predictions:
            logger.warning(f"⚠️ Prédiction active détectée avant envoi #{pred_number}, annulé")
            return
        
        reason_text = to_send.get('reason_text', '')
        logger.info(f"📤 Envoi depuis file: #{pred_number} (canal à #{current_game})")
        success = await send_prediction_multi_channel(pred_number, suit, pred_type, reason_text)
        
        if success:
            prediction_queue.remove(to_send)
            logger.info(f"✅ #{pred_number} envoyé et retiré de la file. Restant: {len(prediction_queue)}")
        else:
            logger.warning(f"⚠️ Échec envoi #{pred_number}, conservation dans file")

# ============================================================================
# TRAITEMENT DES MESSAGES (CORRIGÉ avec Compteur1)
# ============================================================================

async def process_game_result(game_number: int, message_text: str):
    global current_game_number, last_source_game_number, pause_active, pause_end_time
    
    current_game_number = game_number
    last_source_game_number = game_number
    
    # Vérifier si pause expirée
    if pause_active and pause_end_time:
        remaining = (pause_end_time - datetime.now()).total_seconds()
        if remaining <= 0:
            logger.info("⏸️ Pause expirée détectée, reprise automatique")
            await end_pause()
    
    # Reset auto à #1440
    if current_game_number >= 1440:
        logger.warning(f"🚨 RESET #1440 atteint")
        await perform_full_reset("🚨 Reset automatique - Numéro #1440 atteint")
        return
    
    groups = extract_parentheses_groups(message_text)
    if not groups:
        logger.warning(f"⚠️ Pas de groupe trouvé dans #{game_number}")
        return
    
    first_group = groups[0]
    suits_in_first = get_suits_in_group(first_group)
    
    logger.info(f"📊 Jeu #{game_number}: {suits_in_first} dans '{first_group[:30]}...'")
    
    add_to_history(game_number, message_text, first_group, suits_in_first)
    
    # NOUVEAU: Mettre à jour Compteur1 (présences consécutives)
    update_compteur1(game_number, first_group)
    
    # Vérification des prédictions existantes
    await check_prediction_result(game_number, first_group)
    
    # Traiter la file d'attente
    await process_prediction_queue(game_number)
    
    if pause_active:
        logger.info(f"⏸️ En pause, pas de nouvelle détection")
        return
    
    # Compteur2 + validation Compteur3
    if compteur2_active:
        update_compteur2(game_number, first_group)

    # Compteur3 (2ème groupe)
    if compteur3_active and len(groups) >= 2:
        second_group = groups[1]
        update_compteur3(game_number, second_group)

    if compteur2_active or compteur3_active:
        all_preds = get_all_counter_predictions(game_number)
        for predicted_suit, pred_num, pred_type, reason in all_preds:
            added = add_to_prediction_queue(pred_num, predicted_suit, pred_type, reason)
            if added:
                type_labels_log = {
                    'compteur2':    '📊 C2 seul (inverse)',
                    'compteur2_c3': '📊+🔄 C2+C3 (costume C2)',
                    'compteur3_seul': '🔁 C3 seul (inverse)',
                }
                label = type_labels_log.get(pred_type, pred_type)
                logger.info(f"{label}: #{pred_num} ({predicted_suit}) en file d'attente")

async def handle_message(event, is_edit: bool = False):
    try:
        chat = await event.get_chat()
        chat_id = chat.id
        
        if hasattr(chat, 'broadcast') and chat.broadcast:
            if not str(chat_id).startswith('-100'):
                chat_id = int(f"-100{abs(chat_id)}")
        
        normalized_source = normalize_channel_id(SOURCE_CHANNEL_ID)
        if chat_id != normalized_source:
            return
        
        message_text = event.message.message
        edit_info = " [EDITÉ]" if is_edit else ""
        logger.info(f"📨{edit_info} Msg {event.message.id}: {message_text[:60]}...")
        
        if is_message_being_edited(message_text):
            logger.info(f"⏳ Message en cours d'édition (⏰), ignoré")
            if '⏰' in message_text:
                match = re.search(r"#N\s*(\d+)", message_text, re.IGNORECASE)
                if match:
                    waiting_finalization[int(match.group(1))] = {
                        'msg_id': event.message.id,
                        'text': message_text
                    }
            return
        
        if not is_message_finalized(message_text):
            logger.info(f"⏳ Non finalisé ignoré")
            return
        
        match = re.search(r"#N\s*(\d+)", message_text, re.IGNORECASE)
        if not match:
            match = re.search(r"(?:^|[^\d])(\d{3,4})(?:[^\d]|$)", message_text)
        
        if not match:
            logger.warning("⚠️ Numéro non trouvé")
            return
        
        game_number = int(match.group(1))
        
        if game_number in waiting_finalization:
            del waiting_finalization[game_number]
        
        await process_game_result(game_number, message_text)
        
    except Exception as e:
        logger.error(f"❌ Erreur handle_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def handle_new_message(event):
    await handle_message(event, False)

async def handle_edited_message(event):
    await handle_message(event, True)

# ============================================================================
# RESET ET NOTIFICATIONS (CORRIGÉ)
# ============================================================================

async def notify_admin_reset(reason: str, stats: int, queue_stats: int):
    if not ADMIN_ID or ADMIN_ID == 0:
        logger.warning("⚠️ ADMIN_ID non configuré, impossible de notifier")
        return
    
    try:
        admin_entity = await client.get_entity(ADMIN_ID)
        
        msg = f"""🔄 **RESET SYSTÈME**

{reason}

✅ Compteurs internes remis à zéro
✅ {stats} prédictions actives cleared
✅ {queue_stats} prédictions en file cleared
✅ Nouvelle analyse

🤖 Baccarat AI"""
        
        await client.send_message(admin_entity, msg, parse_mode='markdown')
        logger.info(f"✅ Notification reset envoyée à l'admin {ADMIN_ID}")
        
    except Exception as e:
        logger.error(f"❌ Impossible de notifier l'admin: {e}")

async def cleanup_stale_predictions():
    """Nettoie les prédictions bloquées depuis plus de PREDICTION_TIMEOUT_MINUTES."""
    global pending_predictions
    from config import PREDICTION_TIMEOUT_MINUTES
    
    now = datetime.now()
    stale = []
    
    for game_number, pred in list(pending_predictions.items()):
        sent_time = pred.get('sent_time')
        if sent_time:
            age_minutes = (now - sent_time).total_seconds() / 60
            if age_minutes >= PREDICTION_TIMEOUT_MINUTES:
                stale.append(game_number)
    
    for game_number in stale:
        pred = pending_predictions.get(game_number)
        if pred:
            suit = pred.get('suit', '?')
            age = int((now - pred['sent_time']).total_seconds() / 60)
            logger.warning(f"🧹 Prédiction #{game_number} ({suit}) supprimée — bloquée depuis {age} min (timeout {PREDICTION_TIMEOUT_MINUTES} min)")
            
            # Tenter d'éditer le message pour indiquer l'expiration
            try:
                prediction_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
                if prediction_entity and pred.get('message_id'):
                    suit_display = SUIT_DISPLAY.get(suit, suit)
                    expired_msg = f"⏱️ **PRÉDICTION #{game_number}**\n\n🎯 **Couleur:** {suit_display}\n⚠️ **Statut:** EXPIRÉE (timeout)"
                    await client.edit_message(prediction_entity, pred['message_id'], expired_msg, parse_mode='markdown')
            except Exception as e:
                logger.error(f"❌ Impossible d'éditer message expiré #{game_number}: {e}")
            
            del pending_predictions[game_number]
    
    if stale:
        logger.info(f"🧹 {len(stale)} prédiction(s) expirée(s) nettoyée(s)")


async def auto_reset_system():
    """Mode veille avec vérification de pause bloquée et prédictions expirées."""
    global pause_active, pause_end_time
    
    while True:
        try:
            await asyncio.sleep(60)
            
            # Vérifier pause bloquée
            if pause_active and pause_end_time:
                remaining = (pause_end_time - datetime.now()).total_seconds()
                if remaining <= -30:
                    logger.warning("🚨 Pause bloquée détectée (temps dépassé), auto-correction...")
                    await end_pause()
            
            # Nettoyer les prédictions bloquées (timeout)
            if pending_predictions:
                await cleanup_stale_predictions()
                    
        except Exception as e:
            logger.error(f"❌ Erreur auto_reset: {e}")
            await asyncio.sleep(60)

async def perform_full_reset(reason: str):
    global pending_predictions, last_prediction_time, waiting_finalization
    global last_prediction_number_sent, compteur2_trackers, prediction_queue
    global pause_active, pause_counter, pause_cycle_index, pause_message_id, pause_end_time, pause_task
    global compteur1_trackers, compteur1_history, compteur3_trackers

    stats = len(pending_predictions)
    queue_stats = len(prediction_queue)

    # Sauvegarder les séries en cours avant reset
    for tracker in compteur1_trackers.values():
        if tracker.counter >= MIN_CONSECUTIVE_FOR_STATS:
            save_compteur1_series(tracker.suit, tracker.counter, tracker.start_game, tracker.last_game)

    if pause_active:
        pause_active = False
        if pause_task and not pause_task.done():
            pause_task.cancel()
            try:
                await pause_task
            except asyncio.CancelledError:
                pass
        pause_message_id = None
        pause_end_time = None

    for tracker in compteur2_trackers.values():
        tracker.counter = 0
        tracker.last_increment_game = 0

    for tracker in compteur3_trackers.values():
        tracker.counter = 0
        tracker.last_increment_game = 0

    for tracker in compteur1_trackers.values():
        tracker.counter = 0
        tracker.start_game = 0
        tracker.last_game = 0
    
    pending_predictions.clear()
    waiting_finalization.clear()
    prediction_queue.clear()
    last_prediction_time = None
    last_prediction_number_sent = 0
    suit_block_until.clear()
    
    pause_counter = 0
    pause_cycle_index = 0
    
    logger.info(f"🔄 {reason} - {stats} actives cleared, {queue_stats} file cleared, Compteurs reset")
    
    await notify_admin_reset(reason, stats, queue_stats)

# ============================================================================
# COMMANDES ADMIN (NOUVELLES COMMANDES AJOUTÉES)
# ============================================================================

# NOUVEAU: Commande /compteur1 - Voir le statut actuel du Compteur1
async def cmd_compteur1(event):
    global compteur1_trackers
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        lines = [
            "🎯 **COMPTEUR1** (Présences consécutives)",
            "Reset à 0 si le costume manque",
            ""
        ]
        
        for suit in ALL_SUITS:
            tracker = compteur1_trackers.get(suit)
            if tracker:
                if tracker.counter > 0:
                    lines.append(f"{tracker.get_display_name()}: **{tracker.counter}** consécutifs (depuis #{tracker.start_game})")
                else:
                    lines.append(f"{tracker.get_display_name()}: 0")
        
        lines.append(f"\n**Usage:** `/stats` pour voir l'historique des séries ≥3")
        
        await event.respond("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erreur cmd_compteur1: {e}")
        await event.respond(f"❌ Erreur: {e}")

# NOUVEAU: Commande /stats - Voir l'historique des séries Compteur1
async def cmd_stats(event):
    global compteur1_history, compteur1_trackers
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        lines = [
            "📊 **STATISTIQUES COMPTEUR1**",
            "Séries de présences consécutives (minimum 3)",
            ""
        ]
        
        # Sauvegarder les séries en cours avant affichage
        for tracker in compteur1_trackers.values():
            if tracker.counter >= MIN_CONSECUTIVE_FOR_STATS:
                # Vérifier si déjà sauvegardée récemment
                already_saved = False
                for entry in compteur1_history[:5]:  # Vérifier 5 dernières
                    if (entry['suit'] == tracker.suit and 
                        entry['count'] == tracker.counter and
                        entry['end_game'] == tracker.last_game):
                        already_saved = True
                        break
                
                if not already_saved:
                    save_compteur1_series(tracker.suit, tracker.counter, tracker.start_game, tracker.last_game)
        
        # Organiser par costume
        stats_by_suit = {'♥': [], '♠': [], '♦': [], '♣': []}
        for entry in compteur1_history:
            suit = entry['suit']
            if suit in stats_by_suit:
                stats_by_suit[suit].append(entry)
        
        suit_names = {
            '♥': '❤️ Cœur',
            '♠': '♠️ Pique', 
            '♦': '♦️ Carreau',
            '♣': '♣️ Trèfle'
        }
        
        has_data = False
        
        for suit in ['♥', '♠', '♦', '♣']:
            entries = stats_by_suit[suit]
            if not entries:
                continue
            
            has_data = True
            record = get_compteur1_record(suit)
            
            lines.append(f"**{suit_names[suit]}** (Record: {record})")
            
            # Afficher les 5 dernières séries
            for i, entry in enumerate(entries[:5], 1):
                count = entry['count']
                start = entry['start_game']
                end = entry['end_game']
                is_record = "⭐" if count == record else ""
                lines.append(f"  {i}. {count} fois (jeux #{start}-#{end}) {is_record}")
            
            lines.append("")
        
        if not has_data:
            lines.append("❌ Aucune série ≥3 enregistrée encore")
            lines.append("Les séries apparaîtront automatiquement quand un costume")
            lines.append("sera présent 3+ fois consécutivement.")
        
        await event.respond("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erreur cmd_stats: {e}")
        await event.respond(f"❌ Erreur: {e}")

# Commandes existantes (pause, config, etc.)
async def cmd_pause(event):
    global pause_active, pause_counter, pause_cycle_index, PAUSE_CYCLE, PREDICTIONS_BEFORE_PAUSE
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        parts = event.message.message.split()
        
        if len(parts) == 1:
            status_pause = "🟢 ACTIVE" if pause_active else "🔴 INACTIVE"
            
            time_info = ""
            if pause_active and pause_end_time:
                remaining = int((pause_end_time - datetime.now()).total_seconds())
                if remaining > 0:
                    mins = remaining // 60
                    secs = remaining % 60
                    time_info = f"\n⏳ Temps restant: {mins}:{secs:02d}"
                else:
                    time_info = "\n⏳ Temps écoulé (devrait se terminer...)"
            
            current_duration = PAUSE_CYCLE[pause_cycle_index % len(PAUSE_CYCLE)] if not pause_active else "En cours"
            
            await event.respond(
                f"⏸️ **SYSTÈME DE PAUSE**\n\n"
                f"Statut: {status_pause}{time_info}\n\n"
                f"📊 Configuration:\n"
                f"• Cycle: {PAUSE_CYCLE} minutes\n"
                f"• Prochaine pause: {current_duration} min\n"
                f"• Prédictions avant pause: {PREDICTIONS_BEFORE_PAUSE}\n"
                f"• Compteur actuel: {pause_counter}/{PREDICTIONS_BEFORE_PAUSE}\n"
                f"• Cycle actuel: #{pause_cycle_index + 1}\n\n"
                f"**Usage:**\n"
                f"`/pause on` - Activer manuellement\n"
                f"`/pause off` - Désactiver manuellement\n"
                f"`/pausecycle 3,5,4` - Modifier le cycle\n"
                f"`/pauseadd [texte]` - Ajouter expression reprise"
            )
            return
        
        arg = parts[1].lower()
        
        if arg == 'on':
            if pause_active:
                await event.respond("⏸️ Pause déjà active")
                return
            await start_pause()
            await event.respond("✅ **Pause activée manuellement**")
            
        elif arg == 'off':
            if not pause_active:
                await event.respond("▶️ Pas de pause active")
                return
            await end_pause()
            await event.respond("✅ **Pause désactivée manuellement**")
            
        else:
            await event.respond("❌ Usage: `/pause` ou `/pause on/off`")
            
    except Exception as e:
        logger.error(f"Erreur cmd_pause: {e}")
        await event.respond(f"❌ Erreur: {e}")

async def cmd_pausecycle(event):
    global PAUSE_CYCLE
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        parts = event.message.message.split()
        
        if len(parts) == 1:
            cycle_text = f"🔄 **CYCLE DE PAUSE**\n\nCycle actuel: **{PAUSE_CYCLE}** minutes\n\nOrdre des pauses:\n"
            
            for i, duration in enumerate(PAUSE_CYCLE, 1):
                cycle_text += f"{i}. Pause #{i}: {duration} minutes\n"
            
            cycle_text += f"\n**Usage:** `/pausecycle 3,5,4,6` (durées en minutes, séparées par des virgules)"
            await event.respond(cycle_text)
            return
        
        arg = parts[1]
        try:
            new_cycle = [int(x.strip()) for x in arg.split(',')]
            
            if len(new_cycle) < 1:
                await event.respond("❌ Minimum 1 durée requise")
                return
            
            if any(d <= 0 or d > 60 for d in new_cycle):
                await event.respond("❌ Les durées doivent être entre 1 et 60 minutes")
                return
            
            old_cycle = PAUSE_CYCLE
            PAUSE_CYCLE = new_cycle
            
            await event.respond(
                f"✅ **Cycle modifié**\n\n"
                f"Ancien: {old_cycle}\n"
                f"Nouveau: **{PAUSE_CYCLE}**"
            )
            logger.info(f"Admin change cycle pause: {old_cycle} → {PAUSE_CYCLE}")
            
        except ValueError:
            await event.respond("❌ Format invalide. Usage: `/pausecycle 3,5,4`")
            
    except Exception as e:
        logger.error(f"Erreur cmd_pausecycle: {e}")
        await event.respond(f"❌ Erreur: {e}")

async def cmd_pauseadd(event):
    global RESUME_EXPRESSIONS
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        parts = event.message.message.split(' ', 1)
        
        if len(parts) < 2:
            examples = random.sample(RESUME_EXPRESSIONS, min(5, len(RESUME_EXPRESSIONS)))
            examples_text = "\n".join([f"{i+1}. {ex[:50]}..." for i, ex in enumerate(examples)])
            
            await event.respond(
                f"📝 **EXPRESSIONS DE REPRISE**\n\n"
                f"Nombre actuel: **{len(RESUME_EXPRESSIONS)}** expressions\n\n"
                f"Exemples:\n{examples_text}\n\n"
                f"**Usage:** `/pauseadd Votre expression ici - Sossou Kouamé`"
            )
            return
        
        new_expr = parts[1].strip()
        
        if len(new_expr) < 10:
            await event.respond("❌ Expression trop courte (min 10 caractères)")
            return
        
        if len(new_expr) > 200:
            await event.respond("❌ Expression trop longue (max 200 caractères)")
            return
        
        RESUME_EXPRESSIONS.append(new_expr)
        
        await event.respond(
            f"✅ **Expression ajoutée**\n\n"
            f"Total: {len(RESUME_EXPRESSIONS)} expressions\n"
            f"Nouvelle: _{new_expr[:60]}..._"
        )
        logger.info(f"Admin ajoute expression pause: {new_expr[:50]}...")
        
    except Exception as e:
        logger.error(f"Erreur cmd_pauseadd: {e}")
        await event.respond(f"❌ Erreur: {e}")

async def cmd_gap(event):
    global MIN_GAP_BETWEEN_PREDICTIONS
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        parts = event.message.message.split()
        
        if len(parts) == 1:
            await event.respond(
                f"📏 **CONFIGURATION DES ÉCARTS**\n\n"
                f"Écart minimum actuel: **{MIN_GAP_BETWEEN_PREDICTIONS}** numéros\n\n"
                f"**Usage:** `/gap [2-10]`"
            )
            return
        
        arg = parts[1].lower()
        
        try:
            gap_val = int(arg)
            if not 2 <= gap_val <= 10:
                await event.respond("❌ L'écart doit être entre 2 et 10")
                return
            
            old_gap = MIN_GAP_BETWEEN_PREDICTIONS
            MIN_GAP_BETWEEN_PREDICTIONS = gap_val
            
            await event.respond(f"✅ **Écart modifié: {old_gap} → {gap_val}**")
            logger.info(f"Admin change écart: {old_gap} → {gap_val}")
            
        except ValueError:
            await event.respond("❌ Usage: `/gap [2-10]`")
            
    except Exception as e:
        logger.error(f"Erreur cmd_gap: {e}")
        await event.respond(f"❌ Erreur: {e}")

async def cmd_canal_compteur2(event):
    global COMPTEUR2_CHANNEL_ID
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        parts = event.message.message.split()
        
        if len(parts) == 1:
            if COMPTEUR2_CHANNEL_ID:
                await event.respond(
                    f"📊 **CANAL COMPTEUR2**\n\n"
                    f"✅ Actif: `{COMPTEUR2_CHANNEL_ID}`\n\n"
                    f"**Usage:** `/canalcompteur2 [ID]` ou `/canalcompteur2 off`"
                )
            else:
                await event.respond(
                    f"📊 **CANAL COMPTEUR2**\n\n"
                    f"❌ Inactif\n\n"
                    f"**Usage:** `/canalcompteur2 [ID]`"
                )
            return
        
        arg = parts[1].lower()
        
        if arg == 'off':
            old_id = COMPTEUR2_CHANNEL_ID
            COMPTEUR2_CHANNEL_ID = None
            await event.respond(f"❌ **Canal Compteur2 désactivé** (était: `{old_id}`)")
            logger.info(f"Admin désactive canal compteur2")
            return
        
        try:
            new_id = int(arg)
            channel_entity = await resolve_channel(new_id)
            if not channel_entity:
                await event.respond(f"❌ Canal `{new_id}` inaccessible")
                return
            
            old_id = COMPTEUR2_CHANNEL_ID
            COMPTEUR2_CHANNEL_ID = new_id
            
            await event.respond(f"✅ **Canal Compteur2: {old_id} → {new_id}**")
            logger.info(f"Admin change canal compteur2: {old_id} → {new_id}")
            
        except ValueError:
            await event.respond("❌ Usage: `/canalcompteur2 [ID]` ou `/canalcompteur2 off`")
            
    except Exception as e:
        logger.error(f"Erreur cmd_canal_compteur2: {e}")
        await event.respond(f"❌ Erreur: {e}")

async def cmd_canaux(event):
    global COMPTEUR2_CHANNEL_ID, PREDICTION_CHANNEL_ID, SOURCE_CHANNEL_ID
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    lines = [
        "📡 **CONFIGURATION DES CANAUX**",
        "",
        f"📥 **Source:** `{SOURCE_CHANNEL_ID}`",
        f"📤 **Principal:** `{PREDICTION_CHANNEL_ID}`",
        "",
        f"📊 **Compteur2:** {f'`{COMPTEUR2_CHANNEL_ID}`' if COMPTEUR2_CHANNEL_ID else '❌'}",
    ]
    
    await event.respond("\n".join(lines))

async def cmd_queue(event):
    global prediction_queue, current_game_number, MIN_GAP_BETWEEN_PREDICTIONS, PREDICTION_SEND_AHEAD, pause_active
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        if pause_active:
            await event.respond("⏸️ **En pause** - File d'attente figée")
            return
        
        lines = [
            "📋 **FILE D'ATTENTE**",
            f"Écart: {MIN_GAP_BETWEEN_PREDICTIONS} | Envoi: N-{PREDICTION_SEND_AHEAD}",
            "",
        ]
        
        if not prediction_queue:
            lines.append("❌ Vide")
        else:
            lines.append(f"**{len(prediction_queue)} prédictions:**\n")
            
            for i, pred in enumerate(prediction_queue, 1):
                suit = SUIT_DISPLAY.get(pred['suit'], pred['suit'])
                pred_type = pred['type']
                pred_num = pred['game_number']
                
                type_str = "📊C2" if pred_type == 'compteur2' else "🔄C3⚡" if pred_type == 'compteur3_inverse' else "🔁SYN" if pred_type == 'synchro_inverse' else "🤖"

                send_threshold = pred_num - PREDICTION_SEND_AHEAD
                
                if current_game_number >= send_threshold:
                    status = "🟢 PRÊT" if not pending_predictions else "⏳ Attente"
                else:
                    wait_num = send_threshold - current_game_number
                    status = f"⏳ Dans {wait_num}"
                
                lines.append(f"{i}. #{pred_num} {suit} | {type_str} | {status}")
        
        lines.append(f"\n🎮 Canal: #{current_game_number}")
        
        await event.respond("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erreur cmd_queue: {e}")
        await event.respond(f"❌ Erreur: {str(e)}")

async def cmd_compteur2(event):
    global compteur2_seuil_B, compteur2_active, compteur2_trackers
    
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    try:
        parts = event.message.message.split()
        
        if len(parts) == 1:
            status_str = "✅ ON" if compteur2_active else "❌ OFF"
            B = compteur2_seuil_B
            last_game = current_game_number if current_game_number > 0 else "—"

            lines = [
                f"📊 Compteur2: {status_str} | B={B}",
                f"🎮 Dernier jeu reçu: #{last_game}",
                "",
                "Progression (absences):",
            ]

            SUIT_EMOJI = {'♠': '♠️', '♥': '❤️', '♦': '♦️', '♣': '♣️'}
            for suit in ALL_SUITS:
                tracker = compteur2_trackers.get(suit)
                if tracker:
                    count = tracker.counter
                    progress = min(count, B)
                    bar = "█" * progress + "░" * (B - progress)
                    emoji = SUIT_EMOJI.get(suit, suit)
                    lines.append(f"{emoji} : [{bar}] {count}/{B}")

            lines.append("")
            lines.append("Usage: /compteur2 [B/on/off/reset]")

            await event.respond("\n".join(lines))
            return
        
        arg = parts[1].lower()
        
        if arg == 'off':
            compteur2_active = False
            await event.respond("❌ **Compteur2 OFF**")
        elif arg == 'on':
            compteur2_active = True
            await event.respond("✅ **Compteur2 ON**")
        elif arg == 'reset':
            for tracker in compteur2_trackers.values():
                tracker.counter = 0
                tracker.streak_start_game = 0
                tracker.last_increment_game = 0
            await event.respond("🔄 **Compteur2 reset**")
        else:
            try:
                b_val = int(arg)
                if not 2 <= b_val <= 10:
                    await event.respond("❌ B entre 2 et 10")
                    return
                compteur2_seuil_B = b_val
                await event.respond(f"✅ **Seuil B = {b_val}**")
            except ValueError:
                await event.respond("❌ Usage: `/compteur2 [B/on/off/reset]`")
                
    except Exception as e:
        logger.error(f"Erreur cmd_compteur2: {e}")
        await event.respond(f"❌ Erreur: {e}")

async def cmd_history(event):
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    lines = ["📜 **HISTORIQUE**", ""]
    
    recent = prediction_history[:10]
    
    if not recent:
        lines.append("❌ Aucune prédiction")
    else:
        for i, pred in enumerate(recent, 1):
            suit = SUIT_DISPLAY.get(pred['suit'], pred['suit'])
            status = pred['status']
            pred_time = pred['predicted_at'].strftime('%H:%M:%S')
            
            rule = "📊C2" if pred.get('type') == 'compteur2' else "🔄C3⚡" if pred.get('type') == 'compteur3_inverse' else "🔁SYN" if pred.get('type') == 'synchro_inverse' else "🤖"
            emoji = {'en_cours': '🎰', 'gagne_r0': '🏆', 'gagne_r1': '🏆', 'gagne_r2': '🏆', 'perdu': '💔'}.get(status, '❓')
            
            lines.append(f"{i}. {emoji} #{pred['predicted_game']} {suit} | {rule} | {status}")
            lines.append(f"   🕐 {pred_time}")
    
    await event.respond("\n".join(lines))

async def cmd_status(event):
    global compteur2_active, compteur2_seuil_B
    global COMPTEUR2_CHANNEL_ID, pause_active, pause_counter, PREDICTIONS_BEFORE_PAUSE, PAUSE_CYCLE
    global compteur3_active, compteur3_seuil_B2, COMPTEUR3_Z

    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return

    compteur2_str = "✅ ON" if compteur2_active else "❌ OFF"
    compteur3_str = "✅ ON" if compteur3_active else "❌ OFF"
    pause_str = "🟢 ACTIVE" if pause_active else "🔴 INACTIVE"

    now = datetime.now()

    lines = [
        "📊 **STATUT COMPLET**",
        "",
        f"📊 Compteur2: {compteur2_str} (B={compteur2_seuil_B})",
        f"🔄 Compteur3: {compteur3_str} (B2={compteur3_seuil_B2} | Z={COMPTEUR3_Z})",
        f"📏 Écart: {MIN_GAP_BETWEEN_PREDICTIONS}",
        f"⏸️ Pause: {pause_str} ({pause_counter}/{PREDICTIONS_BEFORE_PAUSE})",
        f"🔄 Cycle pause: {PAUSE_CYCLE}",
        f"📋 File: {len(prediction_queue)} | Actives: {len(pending_predictions)}",
        f"🎮 Canal: #{current_game_number}",
        "",
        f"📊 C2: {COMPTEUR2_CHANNEL_ID or '❌'}",
    ]
    
    if pending_predictions:
        lines.append("")
        lines.append("🔍 **En vérification:**")
        for game_number, pred in pending_predictions.items():
            suit_display = SUIT_DISPLAY.get(pred['suit'], pred['suit'])
            rattrapage = pred.get('rattrapage', 0)
            sent_time = pred.get('sent_time')
            age_str = ""
            if sent_time:
                age_sec = int((now - sent_time).total_seconds())
                age_str = f" ({age_sec//60}m{age_sec%60:02d}s)"
            lines.append(f"  • #{game_number} {suit_display} — R{rattrapage}{age_str}")
    
    await event.respond("\n".join(lines))

async def cmd_informations(event):
    """Affiche le compteur détaillé de toutes les prédictions avec raisons claires."""
    global compteur2_seuil_B, compteur2_active, prediction_history

    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return

    try:
        status_str = "✅ ON" if compteur2_active else "❌ OFF"
        B = compteur2_seuil_B

        TYPE_LABELS = {
            'compteur2':      '📊 C2 seul → inverse(C2)',
            'compteur2_c3':   '📊+🔄 C2+C3 → costume C2',
            'compteur3_seul': '🔁 C3 seul → inverse(C3)',
            # anciens types (rétrocompatibilité historique)
            'compteur3_inverse': '🔄 C3 Inverse (legacy)',
            'synchro_inverse':   '🔁 Synchro Inverse (legacy)',
        }
        STATUS_ICONS = {
            'en_cours':  '🎰 En cours',
            'gagne_r0':  '🏆 Gagné R0',
            'gagne_r1':  '🏆 Gagné R1',
            'gagne_r2':  '🏆 Gagné R2',
            'gagne':     '🏆 Gagné',
            'perdu':     '💔 Perdu',
        }

        total = len(prediction_history)

        # Calcul des stats
        nb_gagne = sum(1 for p in prediction_history if 'gagne' in p.get('status', ''))
        nb_perdu = sum(1 for p in prediction_history if p.get('status') == 'perdu')
        nb_cours = sum(1 for p in prediction_history if p.get('status') == 'en_cours')

        def build_lines(preds):
            lines = []
            SEP = "━" * 30
            for i, pred in enumerate(preds, 1):
                suit_disp = SUIT_DISPLAY.get(pred['suit'], pred['suit'])
                status_txt = STATUS_ICONS.get(pred['status'], '❓')
                pred_type = TYPE_LABELS.get(pred['type'], pred['type'])
                reason = pred.get('reason_text', '—')
                pred_time = pred['predicted_at'].strftime('%d/%m %H:%M')

                lines.append(SEP)
                lines.append(f"#{i}  Jeu #{pred['predicted_game']}  →  {suit_disp}")
                lines.append(f"    Statut : {status_txt}")
                lines.append(f"    Type   : {pred_type}")
                lines.append(f"    Heure  : {pred_time}")
                lines.append(f"    Raison :")
                for r_line in reason.split('\n'):
                    if r_line.strip():
                        lines.append(f"      • {r_line.strip()}")
            lines.append(SEP)
            return lines

        MAX_IN_CHAT = 20

        if total == 0:
            await event.respond(
                f"📊 Informations des prédictions : {status_str} | B={B}\n\n"
                f"Inverses: ♠️↔♦️  ❤️↔♣️\n\n"
                f"❌ Aucune prédiction enregistrée."
            )
            return

        header_lines = [
            f"📊 Informations des prédictions : {status_str} | B={B}",
            f"Inverses: ♠️↔♦️  ❤️↔♣️",
            f"",
            f"Total : {total}  |  🏆 {nb_gagne} gagnée(s)  |  💔 {nb_perdu} perdue(s)  |  🎰 {nb_cours} en cours",
            f"",
        ]

        if total <= MAX_IN_CHAT:
            body = build_lines(prediction_history)
            await event.respond("\n".join(header_lines + body))
        else:
            # Générer un PDF
            from io import BytesIO
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
            from reportlab.lib.units import mm
            from reportlab.lib import colors

            buf = BytesIO()
            doc = SimpleDocTemplate(
                buf, pagesize=A4,
                rightMargin=15*mm, leftMargin=15*mm,
                topMargin=15*mm, bottomMargin=15*mm
            )

            styles = getSampleStyleSheet()
            h1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=13, spaceAfter=4)
            h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=10, spaceAfter=3)
            norm = ParagraphStyle('Norm', parent=styles['Normal'], fontSize=9, spaceAfter=2)
            indent = ParagraphStyle('Ind', parent=styles['Normal'], fontSize=9, spaceAfter=2, leftIndent=12)

            def clean(s):
                for old, new in [('♠️','Pique'),('❤️','Coeur'),('♦️','Carreau'),('♣️','Trefle'),
                                  ('♠','Pique'),('♥','Coeur'),('♦','Carreau'),('♣','Trefle'),
                                  ('🏆','[W]'),('💔','[L]'),('🎰','[?]'),('🔄',''),('🔁',''),
                                  ('📊',''),('🎯',''),('▸','>')]:
                    s = s.replace(old, new)
                return s.strip()

            story = []
            story.append(Paragraph(clean(f"Baccarat AI — Predictions ({total} total)"), h1))
            story.append(Paragraph(clean(f"Statut : {status_str} | B={B}"), norm))
            story.append(Paragraph("Inverses : Pique<->Carreau  Coeur<->Trefle", norm))
            story.append(Paragraph(clean(f"Total {total} | Gagne {nb_gagne} | Perdu {nb_perdu} | En cours {nb_cours}"), norm))
            story.append(Spacer(1, 5*mm))

            for i, pred in enumerate(prediction_history, 1):
                suit_disp = SUIT_DISPLAY.get(pred['suit'], pred['suit'])
                status_txt = STATUS_ICONS.get(pred['status'], '?')
                pred_type = TYPE_LABELS.get(pred['type'], pred['type'])
                reason = pred.get('reason_text', '—')
                pred_time = pred['predicted_at'].strftime('%d/%m %H:%M')

                story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
                story.append(Paragraph(
                    clean(f"#{i}  Jeu #{pred['predicted_game']}  ->  {suit_disp}"),
                    h2
                ))
                story.append(Paragraph(clean(f"Statut : {status_txt}"), norm))
                story.append(Paragraph(clean(f"Type   : {pred_type}"), norm))
                story.append(Paragraph(f"Heure  : {pred_time}", norm))
                story.append(Paragraph("Raison :", norm))
                for r_line in reason.split('\n'):
                    if r_line.strip():
                        story.append(Paragraph(clean(f"  • {r_line.strip()}"), indent))
                story.append(Spacer(1, 4*mm))

            doc.build(story)
            buf.seek(0)

            await client.send_file(
                await event.get_input_sender(),
                buf,
                attributes=[],
                caption="\n".join(header_lines),
                file_name="predictions_baccarat.pdf"
            )

    except Exception as e:
        logger.error(f"Erreur cmd_informations: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await event.respond(f"❌ Erreur: {e}")


async def cmd_help(event):
    if event.is_group or event.is_channel:
        return

    help_text = f"""📖 **BACCARAT AI - COMMANDES**

**⚙️ Configuration:**
`/gap [2-10]` - Écart min ({MIN_GAP_BETWEEN_PREDICTIONS})
`/compteur2 [B/on/off/reset]` - Gérer Compteur2

**📊 Compteurs:**
`/compteur1` - Voir Compteur1 (présences)
`/stats` - Historique séries ≥3 (Compteur1)
`/compteur3 [B2/on/off/reset]` - Gérer Compteur3 (2ème groupe)
`/setz [1-20]` - Offset Z : C3 seul → inverse(C3) au numéro source+Z (actuel: {COMPTEUR3_Z})
`/sete [1-20]` - Offset E : C2 seul ou C2+C3 → numéro source+E (actuel: {COMPTEUR3_E})
`/synchro` - Voir synchro C2+C3 inverses (♣↔❤️ / ♦↔♠️)

**📡 Canaux:**
`/canalcompteur2 [ID/off]`
`/canaux` - Voir config

**⏸️ Pause:**
`/pause [on/off]` - Gérer pause
`/pausecycle [3,5,4]` - Modifier cycle
`/pauseadd [texte]` - Ajouter expression

**📋 Gestion:**
`/informations` - Liste complète des prédictions (PDF si trop long)
`/pending` - Prédictions en cours de vérification
`/queue` - File d'attente
`/status` - Statut complet
`/history` - Historique
`/reset` - Reset manuel

ℹ️ **Logique Compteur3:**
Manques groupe 2. Quand C2=♣️ et C3=❤️ (inverses ♣↔❤️/♦↔♠️)
→ Prédit C3 au numéro **dernier+Z**

🤖 Baccarat AI | By Sossou Kouamé"""

    await event.respond(help_text)

async def cmd_pending(event):
    """Affiche les prédictions en cours de vérification."""
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return
    
    from config import PREDICTION_TIMEOUT_MINUTES
    now = datetime.now()
    
    try:
        if not pending_predictions:
            await event.respond("✅ **Aucune prédiction en cours**\n\nLe bot est prêt à envoyer la prochaine.")
            return
        
        lines = [
            f"🔍 **PRÉDICTIONS EN COURS** ({len(pending_predictions)})",
            ""
        ]
        
        for game_number, pred in pending_predictions.items():
            suit = pred.get('suit', '?')
            suit_display = SUIT_DISPLAY.get(suit, suit)
            rattrapage = pred.get('rattrapage', 0)
            current_check = pred.get('current_check', game_number)
            verified_games = pred.get('verified_games', [])
            sent_time = pred.get('sent_time')
            pred_type = pred.get('type', 'standard')
            
            type_str = "📊C2" if pred_type == 'compteur2' else "🔄C3⚡" if pred_type == 'compteur3_inverse' else "🔁SYN" if pred_type == 'synchro_inverse' else "🤖"
            
            age_str = ""
            timeout_str = ""
            if sent_time:
                age_sec = int((now - sent_time).total_seconds())
                age_min = age_sec // 60
                age_sec_r = age_sec % 60
                age_str = f"{age_min}m{age_sec_r:02d}s"
                remaining_min = PREDICTION_TIMEOUT_MINUTES - age_min
                timeout_str = f" | Timeout: {remaining_min}min"
            
            verif_parts = []
            for i in range(3):
                check_num = game_number + i
                if current_check == check_num:
                    verif_parts.append(f"🔵#{check_num}")
                elif check_num in verified_games:
                    verif_parts.append(f"❌#{check_num}")
                else:
                    verif_parts.append(f"⬜#{check_num}")
            
            lines.append(f"**#{game_number}** {suit_display} | {type_str} | R{rattrapage}")
            lines.append(f"  🔍 {' | '.join(verif_parts)}")
            lines.append(f"  ⏱️ Envoyé il y a {age_str}{timeout_str}")
            lines.append("")
        
        lines.append(f"🎮 Canal source: #{current_game_number}")
        
        await event.respond("\n".join(lines))
        
    except Exception as e:
        logger.error(f"Erreur cmd_pending: {e}")
        await event.respond(f"❌ Erreur: {e}")


async def cmd_compteur3(event):
    """Affiche et configure le Compteur3 (manques dans le 2ème groupe)."""
    global compteur3_seuil_B2, compteur3_active, compteur3_trackers, COMPTEUR3_Z

    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return

    try:
        parts = event.message.message.split()

        if len(parts) == 1:
            status_str = "✅ ON" if compteur3_active else "❌ OFF"
            B = compteur3_seuil_B2
            last_game = current_game_number if current_game_number > 0 else "—"

            lines = [
                f"📊 Compteur3: {status_str} | B={B}",
                f"🎮 Dernier jeu reçu: #{last_game}",
                "",
                "Progression (absences):",
            ]

            SUIT_EMOJI = {'♠': '♠️', '♥': '❤️', '♦': '♦️', '♣': '♣️'}
            for suit in ALL_SUITS:
                tracker = compteur3_trackers.get(suit)
                if tracker:
                    count = tracker.counter
                    progress = min(count, B)
                    bar = "█" * progress + "░" * (B - progress)
                    emoji = SUIT_EMOJI.get(suit, suit)
                    lines.append(f"{emoji} : [{bar}] {count}/{B}")

            lines.append("")
            lines.append("Usage: /compteur3 [B/on/off/reset]")

            await event.respond("\n".join(lines))
            return

        arg = parts[1].lower()

        if arg == 'off':
            compteur3_active = False
            await event.respond("❌ **Compteur3 OFF**")
        elif arg == 'on':
            compteur3_active = True
            await event.respond("✅ **Compteur3 ON**")
        elif arg == 'reset':
            for tracker in compteur3_trackers.values():
                tracker.counter = 0
                tracker.last_increment_game = 0
                tracker.streak_start_game = 0
            await event.respond("🔄 **Compteur3 reset**")
        else:
            try:
                b2_val = int(arg)
                if not 1 <= b2_val <= 15:
                    await event.respond("❌ B doit être entre 1 et 15")
                    return
                old_val = compteur3_seuil_B2
                compteur3_seuil_B2 = b2_val
                await event.respond(f"✅ **Seuil B modifié: {old_val} → {b2_val}**")
                logger.info(f"Admin change B3: {old_val} → {b2_val}")
            except ValueError:
                await event.respond("❌ Usage: `/compteur3 [B/on/off/reset]`")

    except Exception as e:
        logger.error(f"Erreur cmd_compteur3: {e}")
        await event.respond(f"❌ Erreur: {e}")


async def cmd_setz(event):
    """Configure la valeur Z (offset numéro pour prédiction inverse C3)."""
    global COMPTEUR3_Z

    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return

    try:
        parts = event.message.message.split()

        if len(parts) == 1:
            await event.respond(
                f"⚡ **VALEUR Z — OFFSET C3 SEUL**\n\n"
                f"Valeur actuelle: **Z = {COMPTEUR3_Z}**\n\n"
                f"Utilisé quand C3 seul atteint B (C2 inverse absent) :\n"
                f"  prédiction = inverse(C3) au numéro **source + Z**\n\n"
                f"**Usage:** `/setz [1-20]`"
            )
            return

        try:
            z_val = int(parts[1])
            if not 1 <= z_val <= 20:
                await event.respond("❌ Z doit être entre 1 et 20")
                return
            old_z = COMPTEUR3_Z
            COMPTEUR3_Z = z_val
            await event.respond(f"✅ **Z modifié: {old_z} → {z_val}**\n\nPrédiction inverse = dernier numéro + {z_val}")
            logger.info(f"Admin change COMPTEUR3_Z: {old_z} → {z_val}")
        except ValueError:
            await event.respond("❌ Usage: `/setz [1-20]`")

    except Exception as e:
        logger.error(f"Erreur cmd_setz: {e}")
        await event.respond(f"❌ Erreur: {e}")


async def cmd_sete(event):
    """Configure la valeur E (offset numéro pour C2 seul ou C2+C3)."""
    global COMPTEUR3_E

    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return

    try:
        parts = event.message.message.split()

        if len(parts) == 1:
            await event.respond(
                f"⚡ **VALEUR E — OFFSET C2**\n\n"
                f"Valeur actuelle: **E = {COMPTEUR3_E}**\n\n"
                f"Utilisé quand :\n"
                f"  • C2 seul atteint B → prédit inverse(C2) au numéro source + E\n"
                f"  • C2 + C3 inverses  → prédit costume C2 au numéro source + E\n\n"
                f"**Usage:** `/sete [1-20]`"
            )
            return

        try:
            e_val = int(parts[1])
            if not 1 <= e_val <= 20:
                await event.respond("❌ E doit être entre 1 et 20")
                return
            old_e = COMPTEUR3_E
            COMPTEUR3_E = e_val
            await event.respond(f"✅ **E modifié: {old_e} → {e_val}**\n\nPrédiction C2 = numéro source + {e_val}")
            logger.info(f"Admin change COMPTEUR3_E: {old_e} → {e_val}")
        except ValueError:
            await event.respond("❌ Usage: `/sete [1-20]`")

    except Exception as e:
        logger.error(f"Erreur cmd_sete: {e}")
        await event.respond(f"❌ Erreur: {e}")


async def cmd_synchro(event):
    """Affiche l'état de synchronisation inverse C2+C3 pour chaque paire.
    Indique si les manquants des deux compteurs sont inverses et ont atteint
    leur seuil B ensemble, sur des numéros consécutifs finalisés (🔰/✅).
    Aucun numéro n'est jamais ignoré : le bot attend toujours qu'un jeu
    soit finalisé (🔰 ou ✅) avant de l'incarner dans les compteurs.
    """
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return

    try:
        pairs_status = get_synchro_status()

        lines = [
            "🔁 **SYNCHRO INVERSE C2 ↔ C3**",
            f"Paires inverses : ♣↔❤️  |  ♦↔♠️",
            f"Seuil C2 (B normal) : **{compteur2_seuil_B}**  |  Seuil C3 (B2) : **{compteur3_seuil_B2}**  |  Z : **{COMPTEUR3_Z}**",
            f"Suivi sur numéros consécutifs finalisés (🔰/✅) uniquement",
            "",
        ]

        for p in pairs_status:
            sc2 = SUIT_DISPLAY.get(p['suit_c2'], p['suit_c2'])
            sc3 = SUIT_DISPLAY.get(p['suit_c3'], p['suit_c3'])

            # Barres de progression
            c2_prog = min(p['c2_counter'], p['c2_threshold'])
            c2_bar = f"[{'█' * c2_prog}{'░' * (p['c2_threshold'] - c2_prog)}]"
            c2_status = "🔮 PRÊT" if p['c2_ready'] else f"{p['c2_counter']}/{p['c2_threshold']}"
            c2_start = f"depuis #{p['c2_streak_start']}" if p['c2_streak_start'] else "pas de série"

            c3_prog = min(p['c3_counter'], p['c3_threshold'])
            c3_bar = f"[{'█' * c3_prog}{'░' * (p['c3_threshold'] - c3_prog)}]"
            c3_status = "🔮 PRÊT" if p['c3_ready'] else f"{p['c3_counter']}/{p['c3_threshold']}"
            c3_start = f"depuis #{p['c3_streak_start']}" if p['c3_streak_start'] else "pas de série"

            if p['synchro']:
                header = f"✅ **SYNCHRO ACTIVE** — {sc2} (C2) ↔ {sc3} (C3)"
                pred_suit = SUIT_DISPLAY.get(p['suit_c2'], p['suit_c2'])
                pred_note = f"  → Prédiction : manque **{pred_suit}** (C2) au jeu dernier+{COMPTEUR3_Z}"
            else:
                header = f"⏳ En attente — {sc2} (C2) ↔ {sc3} (C3)"
                missing_parts = []
                if not p['c2_ready']:
                    missing_parts.append(f"C2 {sc2} : {p['c2_counter']}/{p['c2_threshold']}")
                if not p['c3_ready']:
                    missing_parts.append(f"C3 {sc3} : {p['c3_counter']}/{p['c3_threshold']}")
                pred_note = f"  → Manque encore : {' | '.join(missing_parts)}"

            lines.append(header)
            lines.append(f"  C2 {sc2} : {c2_bar} {c2_status} ({c2_start})")
            lines.append(f"  C3 {sc3} : {c3_bar} {c3_status} ({c3_start})")
            lines.append(pred_note)
            lines.append("")

        lines.append("ℹ️ La prédiction synchro se déclenche automatiquement quand")
        lines.append("C3 et C2 inverses atteignent leur seuil B ensemble.")
        lines.append("Elle prédit le manque du C2 (🔁SYN) avec offset Z.")

        await event.respond("\n".join(lines))

    except Exception as e:
        logger.error(f"Erreur cmd_synchro: {e}")
        await event.respond(f"❌ Erreur: {e}")


async def cmd_reset(event):
    if event.is_group or event.is_channel:
        return
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("🔒 Admin uniquement")
        return

    await event.respond("🔄 Reset...")
    await perform_full_reset("Reset manuel")
    await event.respond("✅ Reset effectué!")

# ============================================================================
# SETUP ET DÉMARRAGE
# ============================================================================

def setup_handlers():
    # Configuration
    client.add_event_handler(cmd_gap, events.NewMessage(pattern=r'^/gap'))
    
    # Canaux
    client.add_event_handler(cmd_canal_compteur2, events.NewMessage(pattern=r'^/canalcompteur2'))
    client.add_event_handler(cmd_canaux, events.NewMessage(pattern=r'^/canaux$'))
    
    # Pause
    client.add_event_handler(cmd_pause, events.NewMessage(pattern=r'^/pause'))
    client.add_event_handler(cmd_pausecycle, events.NewMessage(pattern=r'^/pausecycle'))
    client.add_event_handler(cmd_pauseadd, events.NewMessage(pattern=r'^/pauseadd'))
    
    # Compteurs et stats
    client.add_event_handler(cmd_compteur1, events.NewMessage(pattern=r'^/compteur1$'))
    client.add_event_handler(cmd_stats, events.NewMessage(pattern=r'^/stats$'))
    client.add_event_handler(cmd_compteur3, events.NewMessage(pattern=r'^/compteur3'))
    client.add_event_handler(cmd_setz, events.NewMessage(pattern=r'^/setz'))
    client.add_event_handler(cmd_sete, events.NewMessage(pattern=r'^/sete'))
    client.add_event_handler(cmd_synchro, events.NewMessage(pattern=r'^/synchro$'))
    client.add_event_handler(cmd_informations, events.NewMessage(pattern=r'^/informations$'))

    # Gestion
    client.add_event_handler(cmd_queue, events.NewMessage(pattern=r'^/queue$'))
    client.add_event_handler(cmd_pending, events.NewMessage(pattern=r'^/pending$'))
    client.add_event_handler(cmd_compteur2, events.NewMessage(pattern=r'^/compteur2'))
    client.add_event_handler(cmd_status, events.NewMessage(pattern=r'^/status$'))
    client.add_event_handler(cmd_history, events.NewMessage(pattern=r'^/history$'))
    client.add_event_handler(cmd_reset, events.NewMessage(pattern=r'^/reset$'))
    client.add_event_handler(cmd_help, events.NewMessage(pattern=r'^/help$'))
    
    # Messages
    client.add_event_handler(handle_new_message, events.NewMessage())
    client.add_event_handler(handle_edited_message, events.MessageEdited())

async def start_bot():
    global client, prediction_channel_ok
    
    session = os.getenv('TELEGRAM_SESSION', '')
    client = TelegramClient(StringSession(session), API_ID, API_HASH)
    
    try:
        await client.start(bot_token=BOT_TOKEN)
        setup_handlers()
        initialize_trackers()
        
        if PREDICTION_CHANNEL_ID:
            try:
                pred_entity = await resolve_channel(PREDICTION_CHANNEL_ID)
                if pred_entity:
                    prediction_channel_ok = True
                    logger.info(f"✅ Canal prédiction OK")
            except Exception as e:
                logger.error(f"❌ Erreur canal prédiction: {e}")
        
        logger.info("🤖 Bot démarré")
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur démarrage: {e}")
        return False

async def main():
    try:
        if not await start_bot():
            return
        
        asyncio.create_task(auto_reset_system())
        
        app = web.Application()
        app.router.add_get('/health', lambda r: web.Response(text="OK"))
        app.router.add_get('/', lambda r: web.Response(text="BACCARAT AI 🤖 Running"))
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"🌐 Web server port {PORT}")
        logger.info(f"📏 Écart: {MIN_GAP_BETWEEN_PREDICTIONS}")
        logger.info(f"⏸️ Pause cycle: {PAUSE_CYCLE} min")
        logger.info(f"📡 Multi-canaux: ACTIVE")
        logger.info(f"🎯 Compteur1 (présences): ACTIVE")
        logger.info(f"🔄 Compteur3 (2ème groupe B2={compteur3_seuil_B2} Z={COMPTEUR3_Z}): ACTIVE")
        logger.info(f"✅ Système de pause corrigé")
        
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"❌ Erreur main: {e}")
    finally:
        if client and client.is_connected():
            await client.disconnect()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêté")
    except Exception as e:
        logger.error(f"Fatal: {e}")
        sys.exit(1)
