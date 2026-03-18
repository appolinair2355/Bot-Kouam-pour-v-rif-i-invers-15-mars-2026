import requests
import json


API_URL = "https://1xbet.com/service-api/LiveFeed/GetSportsShortZip"
API_PARAMS = {
    "sports": 236,
    "champs": 2050671,
    "lng": "en",
    "gr": 285,
    "country": 96,
    "virtualSports": "true",
    "groupChamps": "true"
}
API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://1xbet.com/",
}

SUIT_MAP = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}


def _parse_cards(sc_s_list):
    """
    Extrait les cartes joueur et banquier depuis le champ SC.S du jeu.
    sc_s_list = [{"Key":"P","Value":"[{...}]"}, {"Key":"B","Value":"[{...}]"}, ...]
    Retourne (player_cards, banker_cards) sous forme de listes [{S, R}, ...]
    """
    player_cards = []
    banker_cards = []
    for entry in sc_s_list:
        key = entry.get("Key", "")
        val = entry.get("Value", "[]")
        try:
            cards = json.loads(val)
        except Exception:
            cards = []
        if key == "P":
            player_cards = cards
        elif key == "B":
            banker_cards = cards
    return player_cards, banker_cards


def _parse_winner(sc_s_list):
    """Retourne 'Player', 'Banker', 'Tie' ou None."""
    for entry in sc_s_list:
        if entry.get("Key") == "S":
            val = entry.get("Value", "")
            if val == "Win1":
                return "Player"
            elif val == "Win2":
                return "Banker"
            elif val == "Tie":
                return "Tie"
    return None


def get_latest_results():
    """
    Récupère les derniers résultats de Baccara depuis l'API 1xBet.
    Structure réelle de l'API :
      data["Value"] → liste de sports
        sport["L"]  → liste de championnats
          champ["G"] → liste de jeux
    """
    try:
        print("[API] Récupération des résultats depuis l'API...")
        response = requests.get(API_URL, params=API_PARAMS, headers=API_HEADERS, timeout=30)
        data = response.json()

        if "Value" not in data or not isinstance(data["Value"], list):
            print("[API] Structure de réponse inattendue")
            return []

        # Chercher le sport Baccarat (ID=236, N="Baccarat")
        baccara_sport = None
        for sport in data["Value"]:
            if sport.get("N") == "Baccarat" or sport.get("I") == 236:
                if "L" in sport:
                    baccara_sport = sport
                    break

        if baccara_sport is None:
            print("[API] Aucune donnée Baccarat trouvée dans la réponse")
            return []

        results = []

        # sport["L"] contient des championnats, chaque championnat a sport["L"][x]["G"] = jeux
        for championship in baccara_sport["L"]:
            games = championship.get("G", [])
            for game in games:
                if "DI" not in game:
                    continue

                game_number = int(game["DI"])
                sc = game.get("SC", {})
                sc_s = sc.get("S", [])

                # Un jeu terminé a "F": true OU CPS == "Match finished"
                is_finished = game.get("F", False) or sc.get("CPS") == "Match finished"

                player_cards, banker_cards = _parse_cards(sc_s)
                winner = _parse_winner(sc_s)

                # Résumé des cartes pour affichage
                def fmt_cards(cards):
                    return [{"S": SUIT_MAP.get(c.get("S"), "?"), "R": c.get("R", "?"), "raw": c.get("S", -1)} for c in cards]

                result = {
                    "game_number": game_number,
                    "player_cards": fmt_cards(player_cards),
                    "banker_cards": fmt_cards(banker_cards),
                    "winner": winner,
                    "is_finished": is_finished,
                    "score": sc.get("FS", {}),
                }
                results.append(result)

                if is_finished:
                    p_suits = " ".join(c["S"] for c in fmt_cards(player_cards))
                    b_suits = " ".join(c["S"] for c in fmt_cards(banker_cards))
                    print(f"[API] Jeu #{game_number} | Joueur: {p_suits} | Banquier: {b_suits} | Gagnant: {winner} | Terminé: {is_finished}")
                else:
                    print(f"[API] Jeu #{game_number} | En cours / Prématch")

        print(f"[API] Total jeux récupérés: {len(results)} (terminés: {sum(1 for r in results if r['is_finished'])})")
        return results

    except Exception as e:
        print(f"[API] Erreur lors de la récupération: {e}")
        import traceback
        traceback.print_exc()

    return []


def update_history(results, history):
    """Met à jour l'historique avec les jeux terminés."""
    print("[Historique] Mise à jour de l'historique...")
    added = 0
    for result in results:
        if result["is_finished"]:
            game_number = result["game_number"]
            if game_number not in history:
                history[game_number] = {
                    "player_cards": result["player_cards"],
                    "banker_cards": result["banker_cards"],
                    "winner": result.get("winner"),
                    "score": result.get("score"),
                    "is_finished": True
                }
                added += 1
                print(f"[Historique] Jeu #{game_number} ajouté | Gagnant: {result.get('winner')}")
    if added > 0:
        print(f"[Historique] {added} nouveau(x) jeu(x) ajouté(s). Total: {len(history)}")
    return history
