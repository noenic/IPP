import os
import time
import json
import secrets
import requests
import logging
from threading import Thread
from datetime import datetime
from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# ========== Config ==========
STORAGE_DIR = "app_storage"
ICS_DIR = os.path.join(STORAGE_DIR, "ics")
TOKENS_FILE = os.path.join(STORAGE_DIR, "tokens.json")
DOWNLOAD_INTERVAL = 5 * 60  # 5 minutes
BASE_URL = "https://webdfd.mines-ales.fr/planning-eleves/index.php?url=ics/"
os.makedirs(ICS_DIR, exist_ok=True)

# Charger les variables depuis .env
LOGIN_URL = os.getenv("LOGIN_URL")
USERNAME = os.getenv("WEB_USERNAME")
PASSWORD = os.getenv("WEB_PASSWORD")
ADMIN_STATIC_TOKEN = os.getenv("ADMIN_STATIC_TOKEN")
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))

# Parser les sections depuis .env
sections_str = os.getenv("SECTIONS", "")
SECTIONS = {}
for section_suffix in sections_str.split(","):
    if ":" in section_suffix:
        section, suffix = section_suffix.split(":")
        SECTIONS[section] = suffix

# ========== Logging ==========
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ics-proxy")

# ========== Tokens ==========
def load_tokens():
    """Charge les tokens depuis le fichier."""
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_tokens(tokens):
    """Sauvegarde les tokens dans le fichier."""
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)

def generate_token(name: str, tokens: dict) -> str:
    """Génère un token unique pour un nom donné."""
    token = f"{name}_{secrets.token_urlsafe(64)}"
    tokens[name] = token
    save_tokens(tokens)
    return token

def is_valid_token(token_value: str, tokens: dict) -> bool:
    """Vérifie si un token est valide."""
    return token_value in tokens.values()

# ========== Helpers ICS ==========
def get_current_datetime_str() -> str:
    """Retourne la date/heure actuelle au format string."""
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

def add_scrap_note_to_ics(content_bytes: bytes, download_datetime_str: str) -> bytes:
    """
    Ajoute une note de scrap à chaque événement (VEVENT) dans le contenu ICS.
    Retourne le contenu modifié.
    """
    try:
        content_str = content_bytes.decode("utf-8", errors="ignore")
    except Exception:
        content_str = content_bytes.decode("latin-1", errors="ignore")
    content_str = content_str.replace("\r\n", "\n").replace("\r", "\n")
    parts = content_str.split("BEGIN:VEVENT")
    if len(parts) <= 1:
        return content_bytes  # Aucun événement trouvé
    head = parts[0]
    new_parts = [head]
    scrap_note = f"(Scrap le {download_datetime_str})"
    for event_block in parts[1:]:
        if "END:VEVENT" in event_block:
            event_content, rest = event_block.split("END:VEVENT", 1)
            if scrap_note not in event_content:
                if "DESCRIPTION:" in event_content:
                    event_lines = event_content.splitlines(keepends=True)
                    for i, line in enumerate(event_lines):
                        if line.startswith("DESCRIPTION:"):
                            event_lines[i] = line.rstrip() + f"\\n{scrap_note}\n"
                            break
                    event_content = "".join(event_lines)
                else:
                    event_content += f"DESCRIPTION:{scrap_note}\n"
            new_parts.append("BEGIN:VEVENT" + event_content + "END:VEVENT" + rest)
        else:
            new_parts.append("BEGIN:VEVENT" + event_block)
    return "".join(new_parts).replace("\n", "\r\n").encode("utf-8", errors="ignore")

# ========== Download Logic ==========
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'https://webdfd.mines-ales.fr',
    'Referer': 'https://webdfd.mines-ales.fr/planning-eleves/index.php',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
}

def download_section(session, section: str, suffix: str, download_datetime_str: str):
    """Télécharge et sauvegarde le fichier ICS pour une section donnée."""
    try:
        url = BASE_URL + suffix
        log.info("Téléchargement de la section %s depuis %s", section, url)
        response = session.get(url, timeout=25, allow_redirects=True)
        response.raise_for_status()
        if b"<!doctype html" in response.content.lower() and b"connexion" in response.content.lower():
            log.warning("%s -> Page de connexion reçue", section)
            return
        modified_content = add_scrap_note_to_ics(response.content, download_datetime_str)
        dest_path = os.path.join(ICS_DIR, f"{section}.ics")
        with open(dest_path, "wb") as f:
            f.write(modified_content)
        log.info("Fichier ICS sauvegardé pour %s (%d octets)", section, len(modified_content))
    except Exception as e:
        log.error("Erreur lors du téléchargement de %s : %s", section, e)

def download_all_sections_once():
    """Télécharge toutes les sections une fois."""
    log.info("Début du cycle de téléchargement (connexion + toutes les sections).")
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        # Connexion
        session.get(LOGIN_URL, timeout=15, allow_redirects=True)
        payload = {'Username': USERNAME, 'Password': PASSWORD, 'url': '', 'login': ''}
        response = session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)
        if "<!doctype html" in response.text.lower() and "connexion" in response.text.lower():
            log.warning("Échec de la connexion, page de connexion reçue.")
            return
        log.info("Connexion réussie ; téléchargement de %d sections.", len(SECTIONS))
        download_datetime_str = get_current_datetime_str()
        for section, suffix in SECTIONS.items():
            download_section(session, section, suffix, download_datetime_str)
    except Exception as e:
        log.exception("Échec du cycle de connexion/téléchargement : %s", e)

def scheduler():
    """Planifie le téléchargement périodique des sections."""
    while True:
        download_all_sections_once()
        time.sleep(DOWNLOAD_INTERVAL)

# ========== Flask App ==========
app = Flask(__name__)
TOKENS = load_tokens()
Thread(target=scheduler, daemon=True).start()

@app.route("/")
def index():
    """Retourne la liste des sections disponibles."""
    return jsonify({"sections": list(SECTIONS.keys())})

@app.route("/admin/create_token", methods=["POST"])
def create_token():
    """Crée un nouveau token d'accès."""
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {ADMIN_STATIC_TOKEN}":
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "missing name"}), 400
    token = generate_token(name, TOKENS)
    return jsonify({"name": name, "token": token}), 201

@app.route("/<section>")
def get_ics(section):
    """Retourne le fichier ICS pour une section donnée."""
    token_value = request.args.get("token")
    if not token_value or not is_valid_token(token_value, TOKENS):
        return jsonify({"error": "invalid or missing token"}), 401
    matched_section = next((s for s in SECTIONS if s.lower() == section.lower()), None)
    if not matched_section:
        return jsonify({"error": f"section inconnue : {section}"}), 404
    ics_path = os.path.join(ICS_DIR, f"{matched_section}.ics")
    if not os.path.exists(ics_path):
        return jsonify({"error": f"fichier ICS pour {matched_section} non encore téléchargé"}), 503
    # Ajoute la note "Importé le ..." à la volée
    with open(ics_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    now_str = get_current_datetime_str()
    out_lines = []
    in_event = False
    event_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            in_event = True
            event_lines = [line]
        elif stripped == "END:VEVENT":
            event_lines.append(line)
            for i, l in enumerate(event_lines):
                if l.startswith("DESCRIPTION:"):
                    desc_content = l[len("DESCRIPTION:"):].rstrip()
                    desc_content += f"\\n(Importé le {now_str})"
                    event_lines[i] = f"DESCRIPTION:{desc_content}\n"
                    break
            else:
                event_lines.insert(-1, f"DESCRIPTION:(Importé le {now_str})\n")
            out_lines.extend(event_lines)
            in_event = False
        else:
            if in_event:
                event_lines.append(line)
            else:
                out_lines.append(line)
    ics_content = "".join(out_lines)
    return Response(
        ics_content,
        mimetype="text/calendar",
        headers={"Content-Disposition": f"attachment; filename={matched_section}.ics"}
    )

if __name__ == "__main__":
    log.info("Sections disponibles : %s", list(SECTIONS.keys()))
    log.info("Démarrage de l'application sur %s:%d", FLASK_HOST, FLASK_PORT)
    app.run(host=FLASK_HOST, port=FLASK_PORT)
