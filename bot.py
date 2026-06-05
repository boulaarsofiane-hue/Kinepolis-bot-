#!/usr/bin/env python3
"""
Bot Telegram - Prise de commande Kinépolis France
Scraping via AlloCiné — multi-jours, email double, placement, cosy, total
"""

import os
import logging
import threading
import re
import asyncio
from datetime import datetime, timedelta
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

PRIX_NORMAL = 5.0   # € par place
PRIX_COSY   = 7.0   # € par place cosy (+2€ vs standard)

# ─────────────────────────────────────────────
#  ÉTATS
# ─────────────────────────────────────────────
(
    CHOIX_CINEMA,
    CHOIX_JOUR,
    CHOIX_FILM,
    CHOIX_HORAIRE,
    CHOIX_PLACES,
    CHOIX_POSITION_H,
    CHOIX_POSITION_V,
    CHOIX_COSY,
    SAISIE_NOM,
    SAISIE_PRENOM,
    SAISIE_EMAIL,
    CONFIRM_EMAIL,
    CHOIX_PAIEMENT,
    CONFIRMATION,
) = range(14)

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

JOURS_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
MOIS_FR  = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


# ─────────────────────────────────────────────
#  SCRAPING ALLOCINÉ
# ─────────────────────────────────────────────
def scrape_allocine(cinema_id: str, date_str: str) -> list:
    url = f"https://www.allocine.fr/seance/salle_gen_csalle={cinema_id}.html?date={date_str}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"AlloCiné fetch échoué ({cinema_id} {date_str}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    films = []

    for section in soup.select(".movie-card-theater, .card.entity-card, [class*='movie-card']"):
        titre_tag = section.select_one("h2 a, .meta-title a, .title a, h2.meta-title, h2, h3")
        if not titre_tag:
            continue
        titre = titre_tag.get_text(strip=True)
        if not titre or len(titre) < 2 or len(titre) > 120:
            continue

        version = "VF"
        for tag in section.select(".version, .meta-version, [class*='version']"):
            v = tag.get_text(strip=True)
            if v:
                version = v
                break

        seances = []
        for el in section.select(
            "a.showtimes-btn, .showtimes-list a, [class*='showtime'] a, "
            "span.showtimes-time, span, li, a"
        ):
            h = el.get_text(strip=True)
            if re.match(r'^\d{1,2}[h:]\d{2}$', h):
                heure = h.replace("h", ":")
                if not any(s["heure"] == heure for s in seances):
                    seances.append({"heure": heure})

        if titre and seances and not any(f["titre"] == titre for f in films):
            films.append({"titre": titre, "version": version, "seances": seances})

    if not films:
        for h2 in soup.select("h2"):
            titre = h2.get_text(strip=True)
            if not titre or len(titre) < 3 or len(titre) > 120:
                continue
            parent = h2.find_parent()
            if not parent:
                continue
            seances = []
            for el in parent.find_all(string=re.compile(r'^\d{1,2}[h:]\d{2}$')):
                heure = el.strip().replace("h", ":")
                if not any(s["heure"] == heure for s in seances):
                    seances.append({"heure": heure})
            if seances and not any(f["titre"] == titre for f in films):
                films.append({"titre": titre, "version": "VF", "seances": seances})

    return films


def get_jours_disponibles(cinema_id: str) -> list:
    url = f"https://www.allocine.fr/seance/salle_gen_csalle={cinema_id}.html"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jours = []
        for a in soup.select("a[href*='date='], [class*='date'] a, .showtimes-nav a"):
            href = a.get("href", "")
            m = re.search(r'date=(\d{4}-\d{2}-\d{2})', href)
            if m:
                date_str = m.group(1)
                if not any(j["date"] == date_str for j in jours):
                    d = datetime.strptime(date_str, "%Y-%m-%d")
                    delta = (d.date() - datetime.now().date()).days
                    if delta == 0:   label = "Aujourd'hui"
                    elif delta == 1: label = "Demain"
                    else:            label = f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month-1]}"
                    jours.append({"date": date_str, "label": label})
        if jours:
            return jours[:14]
    except Exception as e:
        logger.warning(f"Jours fetch échoué: {e}")

    # Fallback 7 jours
    jours = []
    for i in range(7):
        d = datetime.now() + timedelta(days=i)
        label = "Aujourd'hui" if i == 0 else "Demain" if i == 1 else f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month-1]}"
        jours.append({"date": d.strftime("%Y-%m-%d"), "label": label})
    return jours


def get_programme(cinema_id: str, date_str: str) -> list:
    try:
        return scrape_allocine(cinema_id, date_str)
    except Exception as e:
        logger.error(f"get_programme erreur: {e}")
        return []


# ─────────────────────────────────────────────
#  VALIDATION EMAIL
# ─────────────────────────────────────────────
def email_valide(email: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email.strip()))


# ─────────────────────────────────────────────
#  HELPERS UI
# ─────────────────────────────────────────────
def keyboard_from_list(items: list, cols: int = 1) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(label, callback_data=data) for label, data in items]
    rows = [buttons[i:i+cols] for i in range(0, len(buttons), cols)]
    rows.append([InlineKeyboardButton("❌ Annuler", callback_data="annuler")])
    return InlineKeyboardMarkup(rows)


def calcul_total(nb_places: int, cosy: bool) -> str:
    prix_unit = PRIX_COSY if cosy else PRIX_NORMAL
    total = nb_places * prix_unit
    cosy_txt = f" Cosy ({PRIX_COSY:.0f}€/pl. )" if cosy else f" Standard ({PRIX_NORMAL:.0f}€/pl.)"
    return f"{nb_places} place(s){cosy_txt} = *{total:.0f}€*"


def format_commande(d: dict) -> str:
    cosy = d.get("cosy", False)
    nb   = d.get("nb_places", 0)
    total_txt = calcul_total(nb, cosy)
    return (
        f"🎬 *NOUVELLE COMMANDE KINÉPOLIS*\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"📍 *Cinéma :* {d.get('cinema_nom','?')}\n"
        f"📅 *Jour :* {d.get('jour_label','?')}\n"
        f"🎞 *Film :* {d.get('film_titre','?')}\n"
        f"🕑 *Séance :* {d.get('horaire','?')}\n\n"
        f"🎟 *Places :* {total_txt}\n"
        f"🪑 *Position :* {d.get('position_h','?')} · {d.get('position_v','?')}\n"
        f"🛋 *Cosy :* {'✅ Oui' if cosy else '❌ Non'}\n\n"
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
        f"📍 *{CINEMAS[cid]}*\n\n⏳ Récupération des jours disponibles…",
        parse_mode="Markdown",
    )

    jours = await asyncio.get_event_loop().run_in_executor(None, get_jours_disponibles, cid)
    context.user_data["jours"] = jours

    items = [(j["label"], str(i)) for i, j in enumerate(jours)]
    await query.edit_message_text(
        f"📍 *{CINEMAS[cid]}*\n\n📅 Choisissez un jour :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=2),
    )
    return CHOIX_JOUR


async def choix_jour(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    jour = context.user_data["jours"][int(query.data)]
    context.user_data["jour_date"]  = jour["date"]
    context.user_data["jour_label"] = jour["label"]
    cid = context.user_data["cinema_id"]

    await query.edit_message_text(
        f"📅 *{jour['label']}*\n\n⏳ Récupération du programme…",
        parse_mode="Markdown",
    )

    programme = await asyncio.get_event_loop().run_in_executor(
        None, get_programme, cid, jour["date"]
    )
    context.user_data["programme"] = programme

    if not programme:
        await query.edit_message_text("⚠️ Aucun film disponible pour ce jour.\nTapez /start pour recommencer.")
        return ConversationHandler.END

    items = [(f["titre"], str(i)) for i, f in enumerate(programme)]
    await query.edit_message_text(
        f"📅 *{jour['label']}* — {CINEMAS[cid]}\n\n🎞 Choisissez un film :",
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
        "film_idx":     int(query.data),
        "film_titre":   film["titre"],
        "film_version": film["version"],
    })

    items = [(s["heure"], str(i)) for i, s in enumerate(film["seances"])]
    await query.edit_message_text(
        f"🎬 *{film['titre']}*\n\n🕑 Choisissez un horaire :",
        parse_mode="Markdown",
        reply_markup=keyboard_from_list(items, cols=4),
    )
    return CHOIX_HORAIRE


async def choix_horaire(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    film = context.user_data["programme"][context.user_data["film_idx"]]
    seance = film["seances"][int(query.data)]
    context.user_data["horaire"] = seance["heure"]

    await query.edit_message_text(
        f"🕑 Séance : *{seance['heure']}*\n\n🎟 Combien de places ?",
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
        f"🎟 *{query.data} place(s)*\n\n🪑 Position horizontale :",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⬅️ Gauche",  callback_data="Gauche"),
                InlineKeyboardButton("⬛ Milieu",  callback_data="Milieu"),
                InlineKeyboardButton("➡️ Droite",  callback_data="Droite"),
            ],
            [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
        ]),
    )
    return CHOIX_POSITION_H


async def choix_position_h(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    context.user_data["position_h"] = query.data

    await query.edit_message_text(
        f"🪑 Position : *{query.data}*\n\n🎭 Hauteur dans la salle :",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⬆️ Haut",    callback_data="Haut"),
                InlineKeyboardButton("🔲 Milieu",  callback_data="Milieu"),
                InlineKeyboardButton("⬇️ Bas",     callback_data="Bas"),
            ],
            [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
        ]),
    )
    return CHOIX_POSITION_V


async def choix_position_v(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    context.user_data["position_v"] = query.data

    await query.edit_message_text(
        f"🪑 Position : *{context.user_data['position_h']} · {query.data}*\n\n"
        f"🛋 Souhaitez-vous des places *Cosy* ?\n"
        f"_(Cosy = {PRIX_COSY:.0f}€/place  | Standard = {PRIX_NORMAL:.0f}€/place)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🛋 Oui, Cosy",      callback_data="cosy_oui"),
                InlineKeyboardButton("🪑 Non, Standard",  callback_data="cosy_non"),
            ],
            [InlineKeyboardButton("❌ Annuler", callback_data="annuler")],
        ]),
    )
    return CHOIX_COSY


async def choix_cosy(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "annuler":
        return await annuler(update, context)

    cosy = query.data == "cosy_oui"
    context.user_data["cosy"] = cosy
    nb = context.user_data["nb_places"]
    total_txt = calcul_total(nb, cosy)

    await query.edit_message_text(
        f"🛋 *{'Cosy' if cosy else 'Standard'}* sélectionné\n"
        f"💰 {total_txt}\n\n"
        f"👤 Votre **nom** :",
        parse_mode="Markdown",
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
    email = update.message.text.strip().lower()
    if not email_valide(email):
        await update.message.reply_text("❌ Email invalide. Entrez un email correct (ex: prenom@gmail.com) :")
        return SAISIE_EMAIL
    context.user_data["email_tmp"] = email
    await update.message.reply_text("📧 Confirmez votre adresse email :")
    return CONFIRM_EMAIL


async def confirm_email(update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email2 = update.message.text.strip().lower()
    if email2 != context.user_data.get("email_tmp"):
        await update.message.reply_text(
            "❌ Les deux adresses ne correspondent pas.\n\nReentrez votre **email** :",
            parse_mode="Markdown",
        )
        context.user_data.pop("email_tmp", None)
        return SAISIE_EMAIL

    context.user_data["email"] = email2
    context.user_data.pop("email_tmp", None)

    await update.message.reply_text(
        "✅ Email confirmé !\n\n💳 Mode de paiement :",
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
            InlineKeyboardButton("❌ Annuler",   callback_data="annuler"),
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
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🤖 *Message de ton KINÉPOLIS BOT*\n{'—'*30}\n🆕 NOUVELLE COMMANDE\n\n{recap}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Envoi admin échoué : {e}")

    instrs = {
        "PayPal":              "💳 Vous recevrez un lien PayPal par email.",
        "Virement instantané": "⚡ Les coordonnées bancaires vous seront envoyées par email.",
        "Apple Pay":           "🍎 Un lien Apple Pay vous sera envoyé par email.",
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
            CHOIX_CINEMA:     [CallbackQueryHandler(choix_cinema)],
            CHOIX_JOUR:       [CallbackQueryHandler(choix_jour)],
            CHOIX_FILM:       [CallbackQueryHandler(choix_film)],
            CHOIX_HORAIRE:    [CallbackQueryHandler(choix_horaire)],
            CHOIX_PLACES:     [CallbackQueryHandler(choix_places)],
            CHOIX_POSITION_H: [CallbackQueryHandler(choix_position_h)],
            CHOIX_POSITION_V: [CallbackQueryHandler(choix_position_v)],
            CHOIX_COSY:       [CallbackQueryHandler(choix_cosy)],
            SAISIE_NOM:       [MessageHandler(filters.TEXT & ~filters.COMMAND, saisie_nom)],
            SAISIE_PRENOM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, saisie_prenom)],
            SAISIE_EMAIL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, saisie_email)],
            CONFIRM_EMAIL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_email)],
            CHOIX_PAIEMENT:   [CallbackQueryHandler(choix_paiement)],
            CONFIRMATION:     [CallbackQueryHandler(confirmation)],
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
