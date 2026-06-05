#!/usr/bin/env python3
"""
Bot Telegram - Prise de commande Kinépolis France
"""

import os
import logging
import threading
import asyncio
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from bs4 import BeautifulSoup

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
#  CINÉMAS — code complexe officiel kinepolis.fr
# ─────────────────────────────────────────────
CINEMAS = {
    "FRAMN":  "Kinépolis Amnéville",
    "MTZAM":  "Kinépolis AMPHI – Quartier Muse",
    "FRBLF":  "Kinépolis Belfort",
    "FRBEZ":  "Kinépolis Béziers",
    "KBOUR":  "Kinépolis Bourgoin-Jallieu",
    "BRETI":  "Kinépolis Brétigny-sur-Orge",
    "KFEN":   "Kinépolis Fenouillet (Toulouse)",
    "KLOM":   "Kinépolis Lomme (Lille)",
    "ULONG":  "Kinépolis Longwy",
    "KMUL":   "Kinépolis Mulhouse",
    "KNCY":   "Kinépolis Nancy",
    "KNIM":   "Kinépolis Nîmes",
    "KROU":   "Kinépolis Rouen",
    "KSERV":  "Kinépolis Servon",
    "KMETZ":  "Kinépolis St-Julien-lès-Metz",
    "KTHIO":  "Kinépolis Thionville",
    "WAVES":  "Kinépolis Waves",
}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  SCRAPING PLAYWRIGHT
#  URL réelle : kinepolis.fr/kinepolis_movie_filter_now/CODE
# ─────────────────────────────────────────────
async def scrape_avec_playwright(code: str) -> list:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright non installé")
        return []

    url = f"https://kinepolis.fr/kinepolis_movie_filter_now/{code}/"
    films = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"}
            )
            await page.goto(url, timeout=30000)

            # Attendre le chargement JS
            try:
                await page.wait_for_selector(
                    "article, .movie, .film, [class*='movie'], [class*='film']",
                    timeout=12000,
                )
            except Exception:
                await page.wait_for_timeout(6000)

            html = await page.content()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")

        # Parcourir tous les blocs film possibles
        blocs = soup.select(
            "article, .view-content > div, .movie-item, .film-item, "
            "[class*='movie'], [class*='film-block']"
        )

        for bloc in blocs:
            # Titre
            titre_tag = bloc.select_one(
                "h2, h3, .title, .movie-title, .film-title, "
                "[class*='title']"
            )
            if not titre_tag:
                continue
            titre = titre_tag.get_text(strip=True)
            if not titre or len(titre) < 2 or titre.lower() in ("menu", "accueil", ""):
                continue

            # Version (VF/VO/VOST)
            version_tag = bloc.select_one(".version, .movie-version, [class*='version']")
            version = version_tag.get_text(strip=True) if version_tag else "VF"

            # Séances
            seances = []
            for btn in bloc.select(
                "a[class*='show'], button[class*='show'], "
                "[class*='showtime'], [class*='seance'], [class*='session'], "
                "a[href*='showtime'], a[href*='seance']"
            ):
                heure = btn.get_text(strip=True)
                if heure and any(c.isdigit() for c in heure) and len(heure) <= 10:
                    seances.append({
                        "heure":  heure,
                        "format": btn.get("data-format", ""),
                        "salle":  btn.get("data-room", ""),
                    })

            if titre and seances:
                # Éviter les doublons
                if not any(f["titre"] == titre for f in films):
                    films.append({"titre": titre, "version": version, "seances": seances})

        # Fallback : chercher dans les données JSON embarquées
        if not films:
            import json, re
            for script in soup.find_all("script", type=lambda t: t != "text/css"):
                text = script.string or ""
                for pattern in [r'"title"\s*:\s*"([^"]{3,60})"', r'"titre"\s*:\s*"([^"]{3,60})"']:
                    for titre in re.findall(pattern, text):
                        if titre and not any(f["titre"] == titre for f in films):
                            films.append({
                                "titre": titre,
                                "version": "VF",
                                "seances": [{"heure": "Voir site", "format": "", "salle": ""}],
                            })

    except Exception as e:
        logger.error(f"Playwright erreur pour {code}: {e}")

    return films


def get_programme(code: str) -> list:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(scrape_avec_playwright(code))
        loop.close()
        return result
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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    items = [(nom, code) for code, nom in CINEMAS.items()]
    await update.message.reply_text(
        "👋 Bienvenue sur le *Bot de réservation Kinépolis* !\n\n"
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

    code = query.data
    context.user_data["cinema_code"] = code
    context.user_data["cinema_nom"]  = CINEMAS[code]

    await query.edit_message_text(
        f"📍 *{CINEMAS[code]}* sélectionné.\n\n⏳ Récupération du programme… (15-20 sec)",
        parse_mode="Markdown",
    )

    programme = await asyncio.get_event_loop().run_in_executor(None, get_programme, code)
    context.user_data["programme"] = programme

    if not programme:
        await query.edit_message_text(
            "⚠️ Programme indisponible pour ce cinéma.\n"
            "Tapez /start pour choisir un autre cinéma."
        )
        return ConversationHandler.END

    items = [(f"🎬 {f['titre']} ({f['version']})", str(i)) for i, f in enumerate(programme)]
    await query.edit_message_text(
        f"📍 *{CINEMAS[code]}*\n\n🎞 Choisissez un film :",
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
        f"🎟 *{query.data} place(s)*.\n\n👤 Votre **nom** :",
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
