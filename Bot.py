#!/usr/bin/env python3
"""
Bot Telegram - Prise de commande Kinépolis France
Nécessite : pip install python-telegram-bot==20.7 requests beautifulsoup4
"""

import logging
import json
import asyncio
from datetime import datetime
from typing import Optional

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
#  CONFIG — À REMPLIR
# ─────────────────────────────────────────────
BOT_TOKEN   = "8653431840:AAFfdKi3ypXeeGT19iFLs9yrI8Q85DRkWd4"          # Token @BotFather
ADMIN_ID    = 7712002106                       # Votre Telegram user ID (int)

# ─────────────────────────────────────────────
#  ÉTATS DE LA CONVERSATION
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
#  DONNÉES KINÉPOLIS (slug → nom affiché)
# ─────────────────────────────────────────────
CINEMAS = {
    "kinepolis-amneville":       "Kinépolis Amnéville",
    "kinepolis-bourgoin":        "Kinépolis Bourgoin-Jallieu",
    "kinepolis-bretigny":        "Kinépolis Brétigny-sur-Orge",
    "kinepolis-fenouillet":      "Kinépolis Fenouillet (Toulouse)",
    "kinepolis-lomme":           "Kinépolis Lomme (Lille)",
    "kinepolis-longwy":          "Kinépolis Longwy",
    "kinepolis-metz":            "Kinépolis Metz",
    "kinepolis-mulhouse":        "Kinépolis Mulhouse",
    "kinepolis-nancy":           "Kinépolis Nancy",
    "kinepolis-nimes":           "Kinépolis Nîmes",
    "kinepolis-rouen":           "Kinépolis Rouen",
    "kinepolis-servon":          "Kinépolis Servon",
    "kinepolis-saint-julien":    "Kinépolis Saint-Julien-lès-Metz",
    "kinepolis-thionville":      "Kinépolis Thionville",
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  SCRAPING KINÉPOLIS
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def scrape_programme(cinema_slug: str) -> list[dict]:
    """
    Récupère le programme d'un cinéma Kinépolis.
    Retourne une liste de dicts : {titre, version, url, seances: [{heure, salle, format}]}
    """
    url = f"https://kinepolis.fr/cinemas/{cinema_slug}/programme/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Scraping failed for {cinema_slug}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    films = []

    # Kinépolis utilise des blocs .movie ou .schedule-item selon la version du site
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
            fmt   = btn.get("data-format", "")
            salle = btn.get("data-room", "")
            if heure:
                seances.append({"heure": heure, "format": fmt, "salle": salle})

        if titre and seances:
            lien_tag = bloc.select_one("a[href*='/film/']")
            lien = lien_tag["href"] if lien_tag else ""
            films.append({"titre": titre, "version": version, "url": lien, "seances": seances})

    return films


def get_programme_fallback(cinema_slug: str) -> list[dict]:
    """
    Si le scraping HTML échoue (JS rendu côté client), essaie l'API JSON interne
    que kinepolis.fr utilise parfois.
    """
    api_url = f"https://kinepolis.fr/api/cinemas/{cinema_slug}/program"
    try:
        resp = requests.get(api_url, headers=HEADERS, timeout=10)
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
                        "url": item.get("url", ""),
                        "seances": seances,
                    })
            return films
    except Exception:
        pass
    return []


def get_programme(cinema_slug: str) -> list[dict]:
    programme = scrape_programme(cinema_slug)
    if not programme:
        programme = get_programme_fallback(cinema_slug)
    return programme


# ─────────────────────────────────────────────
#  HELPERS UI
# ─────────────────────────────────────────────
def keyboard_from_list(items: list[tuple[str, str]], cols: int = 2) -> InlineKeyboardMarkup:
    """Génère un clavier inline depuis une liste (label, callback_data)."""
    buttons = [InlineKeyboardButton(label, callback_data=data) for label, data in items]
    rows = [buttons[i:i+cols] for i in range(0, len(buttons), cols)]
    rows.append([InlineKeyboardButton("❌ Annuler", callback_data="annuler")])
    return InlineKeyboardMarkup(rows)


def format_commande(ctx_data: dict) -> str:
    cinema   = ctx_data.get("cinema_nom", "?")
    film     = ctx_data.get("film_titre", "?")
    version  = ctx_data.get("film_version", "")
    horaire  = ctx_data.get("horaire", "?")
    places   = ctx_data.get("nb_places", "?")
    nom      = ctx_data.get("nom", "?")
    prenom   = ctx_data.get("prenom", "?")
    email    = ctx_data.get("email", "?")
    paiement = ctx_data.get("paiement", "?")
    ts       = datetime.now().strftime("%d/%m/%Y %H:%M")

    v = f" ({version})" if version else ""
    return (
        f"🎬 *NOUVELLE COMMANDE KINÉPOLIS*\n"
        f"🕐 {ts}\n\n"
        f"📍 *Cinéma :* {cinema}\n"
        f"🎞 *Film :* {film}{v}\n"
        f"🕑 *Séance :* {horaire}\n"
        f"🎟 *Places :* {places}\n\n"
        f"👤 *Client :*\n"
        f"  Nom : {nom} {prenom}\n"
        f"  Email : {email}\n\n"
        f"💳 *Paiement :* {paiement}\n"
    )


# ─────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    text = (
        "👋 Bienvenue sur le *Bot de réservation Kinépolis* !\n\n"
        "Je vais vous guider pour commander vos places de cinéma en quelques étapes.\n\n"
        "➡️ Choisissez d'abord votre cinéma :"
    )
    items = [(nom, slug) for slug, nom in CINEMAS.items()]
    await update.message.reply_text(
        text,
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
    if slug not in CINEMAS:
        await query.edit_message_text("❌ Cinéma introuvable, tapez /start pour recommencer.")
        return ConversationHandler.END

    context.user_data["cinema_slug"] = slug
    context.user_data["cinema_nom"]  = CINEMAS[slug]

    await query.edit_message_text(
        f"📍 *{CINEMAS[slug]}* sélectionné.\n\n⏳ Récupération du programme en cours…",
        parse_mode="Markdown",
    )

    programme = get_programme(slug)
    context.user_data["programme"] = programme

    if not programme:
        await query.edit_message_text(
            "⚠️ Impossible de récupérer le programme pour ce cinéma.\n"
            "Le site Kinépolis utilise peut-être un rendu JavaScript.\n\n"
            "Tapez /start pour réessayer ou choisir un autre cinéma."
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

    idx = int(query.data)
    programme = context.user_data.get("programme", [])
    film = programme[idx]

    context.user_data["film_idx"]     = idx
    context.user_data["film_titre"]   = film["titre"]
    context.user_data["film_version"] = film["version"]

    seances = film["seances"]
    if not seances:
        await query.edit_message_text("Aucune séance disponible pour ce film.")
        return ConversationHandler.END

    items = []
    for i, s in enumerate(seances):
        label = s["heure"]
        if s.get("format"):
            label += f" — {s['format']}"
        if s.get("salle"):
            label += f" (salle {s['salle']})"
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

    programme = context.user_data["programme"]
    film = programme[context.user_data["film_idx"]]
    seance = film["seances"][int(query.data)]

    horaire_str = seance["heure"]
    if seance.get("format"):
        horaire_str += f" — {seance['format']}"
    if seance.get("salle"):
        horaire_str += f" (salle {seance['salle']})"

    context.user_data["horaire"] = horaire_str

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="1"),
            InlineKeyboardButton("2", callback_data="2"),
            InlineKeyboardButton("3", callback_data="3"),
            InlineKeyboardButton("4", callback_data="4"),
        ],
        [
            InlineKeyboardButton("5", callback_data="5"),
            InlineKeyboardButton("6", callback_data="6"),
            InlineKeyboardButton("7", callback_data="7"),
            InlineKeyboardButton("8", callback_data="8"),
        ],
        [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
    ])

    await query.edit_message_text(
        f"🕑 Séance : *{horaire_str}*\n\n🎟 Combien de places souhaitez-vous ?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return CHOIX_PLACES


async def choix_places(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "annuler":
        return await annuler(update, context)

    context.user_data["nb_places"] = int(query.data)

    await query.edit_message_text(
        f"🎟 *{query.data} place(s)* sélectionnée(s).\n\n"
        "👤 Saisissez votre **nom** :",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return SAISIE_NOM


async def saisie_nom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["nom"] = update.message.text.strip()
    await update.message.reply_text("👤 Saisissez votre **prénom** :", parse_mode="Markdown")
    return SAISIE_PRENOM


async def saisie_prenom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["prenom"] = update.message.text.strip()
    await update.message.reply_text("📧 Saisissez votre **adresse email** :", parse_mode="Markdown")
    return SAISIE_EMAIL


async def saisie_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    # Validation basique
    if "@" not in email or "." not in email:
        await update.message.reply_text("❌ Email invalide. Réessayez :")
        return SAISIE_EMAIL

    context.user_data["email"] = email

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💳 PayPal", callback_data="PayPal"),
            InlineKeyboardButton("⚡ Virement instantané", callback_data="Virement instantané"),
        ],
        [InlineKeyboardButton("🍎 Apple Pay", callback_data="Apple Pay")],
        [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
    ])

    await update.message.reply_text(
        "💳 Choisissez votre mode de paiement :",
        reply_markup=keyboard,
    )
    return CHOIX_PAIEMENT


async def choix_paiement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "annuler":
        return await annuler(update, context)

    context.user_data["paiement"] = query.data

    recap = format_commande(context.user_data)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmer", callback_data="confirmer"),
            InlineKeyboardButton("❌ Annuler", callback_data="annuler"),
        ]
    ])

    await query.edit_message_text(
        recap + "\n\n🔍 *Vérifiez vos informations et confirmez.*",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return CONFIRMATION


async def confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "annuler":
        return await annuler(update, context)

    # ── Notification à l'admin ──
    recap = format_commande(context.user_data)
    user = update.effective_user
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
        admin_notified = True
    except Exception as e:
        logger.error(f"Impossible d'envoyer à l'admin : {e}")
        admin_notified = False

    # ── Message de confirmation au client ──
    paiement = context.user_data.get("paiement", "")
    paiement_instructions = {
        "PayPal": (
            "💳 *PayPal* : Vous recevrez un lien de paiement PayPal par email sous peu.\n"
            "Votre réservation sera confirmée dès réception du paiement."
        ),
        "Virement instantané": (
            "⚡ *Virement instantané* : Les coordonnées bancaires vous seront envoyées par email.\n"
            "Votre réservation sera confirmée dès réception du virement."
        ),
        "Apple Pay": (
            "🍎 *Apple Pay* : Un lien de paiement Apple Pay vous sera envoyé par email.\n"
            "Votre réservation sera confirmée dès réception du paiement."
        ),
    }
    instr = paiement_instructions.get(paiement, "")

    msg = (
        "✅ *Commande enregistrée !*\n\n"
        f"Merci *{context.user_data.get('prenom')} {context.user_data.get('nom')}* "
        "pour votre commande.\n\n"
        f"{instr}\n\n"
        "📧 Un récapitulatif vous sera également envoyé par email.\n\n"
        "_Pour une nouvelle commande, tapez /start_"
    )

    await query.edit_message_text(msg, parse_mode="Markdown")
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
        "🎬 *Bot Kinépolis — Aide*\n\n"
        "/start — Passer une nouvelle commande\n"
        "/annuler — Annuler la commande en cours\n"
        "/aide — Afficher ce message\n",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main() -> None:
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
