#!/usr/bin/env python3
"""
Bot Telegram - Prise de commande Kinépolis France
"""

import os
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
from bs4 import BeautifulSoup

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN = "8653431840:AAFfdKi3ypXeeGT19iFLs9yrI8Q85DRkWd4"
ADMIN_ID  = 7712002106

# ─────────────────────────────────────────────
#  ÉTATS
# ─────────────────────────────────────────────
(
    CHOIX_CINEMA,
    CHOIX_FILM,
    CHOIX_HORAIRE,
    CHOIX_PLACES,
    SAISIE_NOM,
    SAISIE_PRENOM,
    SAISIE_EMAIL,
    CHOIX_PAIEMENT,
    CONFIRMATION,
) = range(9)

# ─────────────────────────────────────────────
#  CINÉMAS
# ─────────────────────────────────────────────
CINEMAS = {
    "kinepolis-amneville":    "Kinépolis Amnéville",
    "kinepolis-bourgoin":     "Kinépolis Bourgoin-Jallieu",
    "kinepolis-bretigny":     "Kinépolis Brétigny-sur-Orge",
    "kinepolis-fenouillet":   "Kinépolis Fenouillet (Toulouse)",
    "kinepolis-lomme":        "Kinépolis Lomme (Lille)",
    "kinepolis-longwy":       "Kinépolis Longwy",
    "kinepolis-metz":         "Kinépolis Metz",
    "kinepolis-mulhouse":     "Kinépolis Mulhouse",
    "kinepolis-nancy":        "Kinépolis Nancy",
    "kinepolis-nimes":        "Kinépolis Nîmes",
    "kinepolis-rouen":        "Kinépolis Rouen",
    "kinepolis-servon":       "Kinépolis Servon",
    "kinepolis-saint-julien": "Kinépolis Saint-Julien-lès-Metz",
    "kinepolis-thionville":   "Kinépolis Thionville",
}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────
def scrape_programme(cinema_slug: str) -> list:
    url = f"https://kinepolis.fr/cinemas/{cinema_slug}/programme/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Scraping échoué pour {cinema_slug}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    films = []

    for bloc in soup.select(".schedule-item, .movie-schedule, article.movie"):
        titre_tag = bloc.select_one(".movie-title, h2.title, .schedule-title")
        if not titre_tag:
            continue
        titre = titre_tag.get_text(strip=True)
        version_tag = bloc.select_one(".version, .movie-version")
        version = version_tag.get_text(strip=True) if version_tag else "VF"
        seances = []
        for btn in bloc.select("a.showtime, button.showtime, .showtime-btn, .session"):
            heure = btn.get_text(strip=True)
            if heure:
                seances.append({
                    "heure": heure,
                    "format": btn.get("data-format", ""),
                    "salle": btn.get("data-room", ""),
                })
        if titre and seances:
            films.append({"titre": titre, "version": version, "seances": seances})

    return films


def get_programme_fallback(cinema_slug: str) -> list:
    try:
        resp = requests.get(
            f"https://kinepolis.fr/api/cinemas/{cinema_slug}/program",
            headers=HEADERS, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            films = []
            for item in data.get("movies", data.get("films", [])):
                titre = item.get("title", item.get("titre", "?"))
                seances = []
                for s in item.get("showtimes", item.get("seances", [])):
                    heure = s.get("time", s.get("heure", ""))
                    if heure:
                        seances.append({
                            "heure": heure,
                            "format": s.get("format", ""),
                            "salle": s.get("room", ""),
                        })
                if seances:
                    films.append({
                        "titre": titre,
                        "version": item.get("version", "VF"),
                        "seances": seances,
                    })
            return films
    except Exception:
        pass
    return []


def get_programme(cinema_slug: str) -> list:
    prog = scrape_programme(cinema_slug)
    if not prog:
        prog = get_programme_fallback(cinema_slug)
    return prog


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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    items = [(nom, slug) for slug, nom in CINEMAS.items()]
    await update.message.reply_text(
        "👋 Bienvenue sur le *Bot de réservation Kinépolis* !\n\n"
        "Je vais vous guider pour commander vos places en quelques étapes.\n\n"
        "➡️ Choisissez votre cinéma :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=1),
    )
    return CHOIX_CINEMA


async def choix_cinema(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    slug = query.data
    context.user_data["cinema_slug"] = slug
    context.user_data["cinema_nom"]  = CINEMAS[slug]

    await query.edit_message_text(
        f"📍 *{CINEMAS[slug]}* sélectionné.\n\n⏳ Récupération du programme…",
        parse_mode="Markdown",
    )

    programme = get_programme(slug)
    context.user_data["programme"] = programme

    if not programme:
        await query.edit_message_text(
            "⚠️ Programme indisponible pour ce cinéma (site JS).\n"
            "Tapez /start pour choisir un autre cinéma."
        )
        return ConversationHandler.END

    items = [(f"🎬 {f['titre']} ({f['version']})", str(i)) for i, f in enumerate(programme)]
    await query.edit_message_text(
        f"📍 *{CINEMAS[slug]}*\n\n🎞 Choisissez un film :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=1),
    )
    return CHOIX_FILM


async def choix_film(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    film = context.user_data["programme"][int(query.data)]
    context.user_data.update({
        "film_idx":     int(query.data),
        "film_titre":   film["titre"],
        "film_version": film["version"],
    })

    items = []
    for i, s in enumerate(film["seances"]):
        label = s["heure"]
        if s.get("format"): label += f" — {s['format']}"
        if s.get("salle"):  label += f" (salle {s['salle']})"
        items.append((label, str(i)))

    await query.edit_message_text(
        f"🎬 *{film['titre']}* ({film['version']})\n\n🕑 Choisissez un horaire :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=2),
    )
    return CHOIX_HORAIRE


async def choix_horaire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    film   = context.user_data["programme"][context.user_data["film_idx"]]
    seance = film["seances"][int(query.data)]
    h = seance["heure"]
    if seance.get("format"): h += f" — {seance['format']}"
    if seance.get("salle"):  h += f" (salle {seance['salle']})"
    context.user_data["horaire"] = h

    await query.edit_message_text(
        f"🕑 Séance : *{h}*\n\n🎟 Combien de places ?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(str(n), callback_data=str(n)) for n in range(1, 5)],
            [InlineKeyboardButton(str(n), callback_data=str(n)) for n in range(5, 9)],
            [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
        ]),
    )
    return CHOIX_PLACES


async def choix_places(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    context.user_data["nb_places"] = int(query.data)
    await query.edit_message_text(
        f"🎟 *{query.data} place(s)* sélectionnée(s).\n\n👤 Votre **nom** :",
        parse_mode="Markdown",
    )
    return SAISIE_NOM


async def saisie_nom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nom"] = update.message.text.strip()
    await update.message.reply_text("👤 Votre **prénom** :", parse_mode="Markdown")
    return SAISIE_PRENOM


async def saisie_prenom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["prenom"] = update.message.text.strip()
    await update.message.reply_text("📧 Votre **adresse email** :", parse_mode="Markdown")
    return SAISIE_EMAIL


async def saisie_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Email invalide, réessayez :")
        return SAISIE_EMAIL

    context.user_data["email"] = email
    await update.message.reply_text(
        "💳 Mode de paiement :",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💳 PayPal", callback_data="PayPal"),
                InlineKeyboardButton("⚡ Virement instantané", callback_data="Virement instantané"),
            ],
            [InlineKeyboardButton("🍎 Apple Pay", callback_data="Apple Pay")],
            [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
        ]),
    )
    return CHOIX_PAIEMENT


async def choix_paiement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            InlineKeyboardButton("❌ Annuler",   callback_data="annuler"),
        ]]),
    )
    return CONFIRMATION


async def confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆕 NOUVELLE COMMANDE\n\n{recap}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Envoi admin échoué : {e}")

    paiement = context.user_data.get("paiement", "")
    instrs = {
        "PayPal":               "💳 Vous recevrez un lien PayPal par email.",
        "Virement instantané":  "⚡ Les coordonnées bancaires vous seront envoyées par email.",
        "Apple Pay":            "🍎 Un lien Apple Pay vous sera envoyé par email.",
    }

    await query.edit_message_text(
        f"✅ *Commande enregistrée !*\n\n"
        f"Merci *{context.user_data.get('prenom')} {context.user_data.get('nom')}* !\n\n"
        f"{instrs.get(paiement, '')}\n\n"
        "_Pour une nouvelle commande, tapez /start_",
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


async def annuler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "❌ Commande annulée. Tapez /start pour recommencer."
    if update.callback_query:
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)
    context.user_data.clear()
    return ConversationHandler.END


async def aide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎬 *Bot Kinépolis*\n\n"
        "/start — Nouvelle commande\n"
        "/annuler — Annuler la commande en cours\n"
        "/aide — Ce message",
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
    def log_message(self, *args):
        pass


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
