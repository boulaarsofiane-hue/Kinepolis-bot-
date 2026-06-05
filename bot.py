#!/usr/bin/env python3
"""
Bot Telegram - Prise de commande Kinépolis France
Scraping via AlloCiné (HTML stable, pas de JS)
"""

import os
import logging
import threading
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
from bs4 import BeautifulSoup

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters,
)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN = "8653431840:AAFfdKi3ypXeeGT19iFLs9yrI8Q85DRkWd4"
ADMIN_ID  = 7712002106

# ─────────────────────────────────────────────
#  ÉTATS
# ─────────────────────────────────────────────
(CHOIX_CINEMA, CHOIX_FILM, CHOIX_HORAIRE, CHOIX_PLACES,
 SAISIE_NOM, SAISIE_PRENOM, SAISIE_EMAIL, CHOIX_PAIEMENT, CONFIRMATION) = range(9)

# ─────────────────────────────────────────────
#  CINÉMAS — IDs AlloCiné officiels
# ─────────────────────────────────────────────
CINEMAS = {
    "P0904":  "Kinépolis Amnéville",
    "P3052":  "Kinépolis AMPHI – Quartier Muse",
    "P1699":  "Kinépolis Belfort",
    "P2275":  "Kinépolis Béziers",
    "P0896":  "Kinépolis Bourgoin-Jallieu",
    "P0929":  "Kinépolis Brétigny-sur-Orge",
    "W3115":  "Kinépolis Fenouillet (Toulouse)",
    "P9j0GX": "Kinépolis Lomme (Lille)",
    "P2060":  "Kinépolis Longwy",
    "P0774":  "Kinépolis Mulhouse",
    "P1665":  "Kinépolis Nancy",
    "P0923":  "Kinépolis Nîmes",
    "P0678":  "Kinépolis Rouen",
    "P2063":  "Kinépolis Servon",
    "P0178":  "Kinépolis St-Julien-lès-Metz",
    "P1795":  "Kinépolis Thionville",
    "W5716":  "Kinépolis Waves",
}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# ─────────────────────────────────────────────
#  SCRAPING ALLOCINÉ
# ─────────────────────────────────────────────
def scrape_allocine(cinema_id: str) -> list:
    """Scrape les films et séances du jour sur AlloCiné."""
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://www.allocine.fr/seance/salle_gen_csalle={cinema_id}.html"
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"AlloCiné fetch échoué ({cinema_id}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    films = []

    # AlloCiné structure : sections par film avec class "card entity-card"
    for section in soup.select(".movie-card-theater, .card.entity-card, [class*='movie-card']"):
        # Titre
        titre_tag = section.select_one("h2 a, .meta-title a, .title a, h2.meta-title")
        if not titre_tag:
            titre_tag = section.select_one("h2, h3, .title")
        if not titre_tag:
            continue
        titre = titre_tag.get_text(strip=True)
        if not titre or len(titre) < 2:
            continue

        # Version
        version = "VF"
        for tag in section.select(".version, .meta-version, [class*='version']"):
            v = tag.get_text(strip=True)
            if v:
                version = v
                break

        # Horaires — boutons de séances
        seances = []
        for btn in section.select("a.showtimes-btn, .showtimes-list a, [class*='showtime'] a, span.showtimes-time"):
            h = btn.get_text(strip=True)
            # Filtre : ne garder que les vraies heures (ex: "14:30", "20h15")
            if re.match(r'^\d{1,2}[h:]\d{2}$', h):
                seances.append({"heure": h.replace("h", ":"), "format": "", "salle": ""})

        # Deuxième tentative avec les spans d'horaire
        if not seances:
            for span in section.select("span, li"):
                h = span.get_text(strip=True)
                if re.match(r'^\d{1,2}[h:]\d{2}$', h):
                    seances.append({"heure": h.replace("h", ":"), "format": "", "salle": ""})

        if titre and seances:
            if not any(f["titre"] == titre for f in films):
                films.append({"titre": titre, "version": version, "seances": seances})

    # Si rien trouvé, essai avec une structure plus large
    if not films:
        for h2 in soup.select("h2"):
            titre = h2.get_text(strip=True)
            if not titre or len(titre) < 3 or len(titre) > 100:
                continue
            # Chercher les horaires dans le parent
            parent = h2.find_parent()
            if not parent:
                continue
            seances = []
            for el in parent.find_all(string=re.compile(r'^\d{1,2}[h:]\d{2}$')):
                h = el.strip().replace("h", ":")
                seances.append({"heure": h, "format": "", "salle": ""})
            if seances and not any(f["titre"] == titre for f in films):
                films.append({"titre": titre, "version": "VF", "seances": seances})

    return films


def get_programme(cinema_id: str) -> list:
    try:
        return scrape_allocine(cinema_id)
    except Exception as e:
        logger.error(f"get_programme erreur: {e}")
        return []


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def keyboard_from_list(items: list, cols: int = 2) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(label, callback_data=data) for label, data in items]
    rows = [buttons[i:i+cols] for i in range(0, len(buttons), cols)]
    rows.append([InlineKeyboardButton("❌ Annuler", callback_data="annuler")])
    return InlineKeyboardMarkup(rows)


def format_commande(d: dict) -> str:
    v = f" ({d.get('film_version', '')})" if d.get("film_version") else ""
    return (
        f"🎬 *NOUVELLE COMMANDE KINÉPOLIS*\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"📍 *Cinéma :* {d.get('cinema_nom','?')}\n"
        f"🎞 *Film :* {d.get('film_titre','?')}{v}\n"
        f"🕑 *Séance :* {d.get('horaire','?')}\n"
        f"🎟 *Places :* {d.get('nb_places','?')}\n\n"
        f"👤 *Client :*\n"
        f"  Nom : {d.get('nom','?')} {d.get('prenom','?')}\n"
        f"  Email : {d.get('email','?')}\n\n"
        f"💳 *Paiement :* {d.get('paiement','?')}\n"
    )


# ─────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────
async def start(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    items = [(nom, cid) for cid, nom in CINEMAS.items()]
    await update.message.reply_text(
        "👋 Bienvenue sur le *Bot de réservation Kinépolis* !\n\n➡️ Choisissez votre cinéma :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=1),
    )
    return CHOIX_CINEMA


async def choix_cinema(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    cid = query.data
    context.user_data["cinema_id"]  = cid
    context.user_data["cinema_nom"] = CINEMAS[cid]

    await query.edit_message_text(
        f"📍 *{CINEMAS[cid]}* sélectionné.\n\n⏳ Récupération du programme…",
        parse_mode="Markdown",
    )

    import asyncio
    programme = await asyncio.get_event_loop().run_in_executor(None, get_programme, cid)
    context.user_data["programme"] = programme

    if not programme:
        await query.edit_message_text(
            "⚠️ Programme indisponible pour ce cinéma.\nTapez /start pour en choisir un autre."
        )
        return ConversationHandler.END

    items = [(f["titre"], str(i)) for i, f in enumerate(programme)]
    await query.edit_message_text(
        f"📍 *{CINEMAS[cid]}*\n\n🎞 Choisissez un film :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=1),
    )
    return CHOIX_FILM


async def choix_film(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    film = context.user_data["programme"][int(query.data)]
    context.user_data.update({
        "film_idx": int(query.data),
        "film_titre": film["titre"],
        "film_version": film["version"],
    })
    items = [(s["heure"], str(i)) for i, s in enumerate(film["seances"])]

    await query.edit_message_text(
        f"🎬 *{film['titre']}* ({film['version']})\n\n🕑 Choisissez un horaire :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=3),
    )
    return CHOIX_HORAIRE


async def choix_horaire(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    film = context.user_data["programme"][context.user_data["film_idx"]]
    seance = film["seances"][int(query.data)]
    h = seance["heure"]
    context.user_data["horaire"] = h

    await query.edit_message_text(
        f"🕑 Séance : *{h}*\n\n🎟 Combien de places ?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(str(n), callback_data=str(n)) for n in range(1, 6)],
            [InlineKeyboardButton(str(n), callback_data=str(n)) for n in range(6, 11)],
            [InlineKeyboardButton(str(n), callback_data=str(n)) for n in range(11, 16)],
            [InlineKeyboardButton(str(n), callback_data=str(n)) for n in range(16, 21)],
            [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
        ]),
    )
    return CHOIX_PLACES


async def choix_places(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)
    context.user_data["nb_places"] = int(query.data)
    await query.edit_message_text(
        f"🎟 *{query.data} place(s)*.\n\n👤 Votre **nom** :", parse_mode="Markdown"
    )
    return SAISIE_NOM


async def saisie_nom(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nom"] = update.message.text.strip()
    await update.message.reply_text("👤 Votre **prénom** :", parse_mode="Markdown")
    return SAISIE_PRENOM


async def saisie_prenom(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["prenom"] = update.message.text.strip()
    await update.message.reply_text("📧 Votre **adresse email** :", parse_mode="Markdown")
    return SAISIE_EMAIL


async def saisie_email(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Email invalide, réessayez :")
        return SAISIE_EMAIL
    context.user_data["email"] = email
    await update.message.reply_text(
        "💳 Mode de paiement :",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 PayPal", callback_data="PayPal"),
             InlineKeyboardButton("⚡ Virement instantané", callback_data="Virement instantané")],
            [InlineKeyboardButton("🍎 Apple Pay", callback_data="Apple Pay")],
            [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
        ]),
    )
    return CHOIX_PAIEMENT


async def choix_paiement(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)
    context.user_data["paiement"] = query.data
    recap = format_commande(context.user_data)
    await query.edit_message_text(
        recap + "\n🔍 *Vérifiez et confirmez.*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirmer", callback_data="confirmer"),
            InlineKeyboardButton("❌ Annuler", callback_data="annuler"),
        ]]),
    )
    return CONFIRMATION


async def confirmation(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    user  = update.effective_user
    recap = format_commande(context.user_data)
    recap += (
        f"\n📬 *Commande de :* [{user.full_name}](tg://user?id={user.id})\n"
        f"  @{user.username or 'sans pseudo'} | ID: `{user.id}`"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"🆕 NOUVELLE COMMANDE\n\n{recap}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Envoi admin échoué : {e}")

    instrs = {
        "PayPal": "💳 Vous recevrez un lien PayPal par email.",
        "Virement instantané": "⚡ Les coordonnées bancaires vous seront envoyées par email.",
        "Apple Pay": "🍎 Un lien Apple Pay vous sera envoyé par email.",
    }
    await query.edit_message_text(
        f"✅ *Commande enregistrée !*\n\n"
        f"Merci *{context.user_data.get('prenom')} {context.user_data.get('nom')}* !\n\n"
        f"{instrs.get(context.user_data.get('paiement',''), '')}\n\n"
        "_Pour une nouvelle commande, tapez /start_",
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


async def annuler(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "❌ Commande annulée. Tapez /start pour recommencer."
    if update.callback_query:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)
    context.user_data.clear()
    return ConversationHandler.END


async def aide(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎬 *Bot Kinépolis*\n\n/start — Nouvelle commande\n/annuler — Annuler\n/aide — Ce message",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
#  SERVEUR WEB (requis par Render)
# ─────────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

def run_web():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main() -> None:
    threading.Thread(target=run_web, daemon=True).start()
    logger.info("Serveur web démarré")

    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOIX_CINEMA:   [CallbackQueryHandler(choix_cinema)],
            CHOIX_FILM:     [CallbackQueryHandler(choix_film)],
            CHOIX_HORAIRE:  [CallbackQueryHandler(choix_horaire)],
            CHOIX_PLACES:   [CallbackQueryHandler(choix_places)],
            SAISIE_NOM:     [MessageHandler(filters.TEXT & ~filters.COMMAND, saisie_nom)],
            SAISIE_PRENOM:  [MessageHandler(filters.TEXT & ~filters.COMMAND, saisie_prenom)],
            SAISIE_EMAIL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, saisie_email)],
            CHOIX_PAIEMENT: [CallbackQueryHandler(choix_paiement)],
            CONFIRMATION:   [CallbackQueryHandler(confirmation)],
        },
        fallbacks=[CommandHandler("annuler", annuler)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("aide", aide))
    app.add_handler(CommandHandler("help", aide))
    logger.info("Bot démarré — en attente de messages…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
