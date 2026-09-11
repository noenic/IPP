from __future__ import annotations
import os
import time
import json
import secrets
import logging
from dataclasses import dataclass
from datetime import datetime
from threading import Thread
from typing import Callable
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response

load_dotenv()

# ========== Constantes ==========
STORAGE_DIR = "app_storage"
ICS_DIR = os.path.join(STORAGE_DIR, "ics")
TOKENS_FILE = os.path.join(STORAGE_DIR, "tokens.json")
BASE_URL = "https://webdfd.mines-ales.fr/planning-eleves/index.php?url=ics/"
os.makedirs(ICS_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://webdfd.mines-ales.fr",
    "Referer": "https://webdfd.mines-ales.fr/planning-eleves/index.php",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ========== Logging ==========
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ics-proxy")


# ========== Configuration ==========
@dataclass(frozen=True)
class Config:
    download_interval: int
    login_url: str
    username: str
    password: str
    admin_static_token: str
    flask_host: str
    flask_port: int
    sections: dict[str, str]
    add_import_note: bool
    add_place_note: bool
    add_scrap_note: bool

    @classmethod
    def from_env(cls) -> "Config":
        def env_bool(name: str, default: bool = True) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on", "oui"}

        sections: dict[str, str] = {}
        for section_suffix in os.getenv("SECTIONS", "").split(","):
            if ":" in section_suffix:
                section, suffix = section_suffix.split(":")
                sections[section] = suffix
        return cls(
            download_interval=int(os.getenv("DOWNLOAD_INTERVAL", 5 * 60)),
            login_url=os.getenv("LOGIN_URL"),
            username=os.getenv("WEB_USERNAME"),
            password=os.getenv("WEB_PASSWORD"),
            admin_static_token=os.getenv("ADMIN_STATIC_TOKEN"),
            flask_host=os.getenv("FLASK_HOST", "0.0.0.0"),
            flask_port=int(os.getenv("FLASK_PORT", 5000)),
            sections=sections,
            add_import_note=env_bool("ADD_IMPORT_NOTE"),
            add_place_note=env_bool("ADD_PLACE_NOTE"),
            add_scrap_note=env_bool("ADD_SCRAP_NOTE"),
        )


CONFIG = Config.from_env()


# ========== Tokens ==========
class TokenStore:
    """Gère la persistance et la validation des tokens d'accès."""

    def __init__(self, path: str):
        self._path = path
        self._tokens: dict[str, str] = self._load()
        self._valid_values: set[str] = set(self._tokens.values())

    def _load(self) -> dict[str, str]:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._tokens, f, indent=2)

    def create(self, name: str) -> str:
        """Génère et persiste un nouveau token pour `name`."""
        token = f"{name}_{secrets.token_urlsafe(64)}"
        self._tokens[name] = token
        self._valid_values.add(token)
        self._save()
        return token

    def is_valid(self, token_value: str) -> bool:
        return token_value in self._valid_values


token_store = TokenStore(TOKENS_FILE)


# ========== Helpers ICS ==========
def get_current_datetime_str() -> str:
    """Retourne la date/heure actuelle au format string."""
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def _decode_ics_bytes(content_bytes: bytes) -> str:
    """Décode un flux ICS en UTF-8, avec repli sur Latin-1 si besoin."""
    try:
        return content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return content_bytes.decode("latin-1")


def _find_field_index(event_lines: list[str], field: str) -> int | None:
    """Index de la première ligne `FIELD:...` dans un bloc VEVENT, ou None."""
    prefix = f"{field}:"
    for i, line in enumerate(event_lines):
        if line.startswith(prefix):
            return i
    return None


def _extract_field_value(event_lines: list[str], field: str) -> str | None:
    """Valeur brute de la première ligne `FIELD:...`, ou None si absente."""
    idx = _find_field_index(event_lines, field)
    if idx is None:
        return None
    return event_lines[idx][len(field) + 1:].rstrip()


def _append_notes_to_description(
    event_lines: list[str], notes: list[str], skip_if_present: bool
) -> list[str]:
    """Ajoute une ou plusieurs notes dans la DESCRIPTION d'un bloc VEVENT.

    - Si le champ DESCRIPTION existe déjà, chaque note est ajoutée à la
      suite, séparée par l'échappement ICS "\\n".
    - Sinon, un nouveau champ DESCRIPTION est créé juste avant END:VEVENT.
    - Si `skip_if_present`, une note déjà présente dans le bloc n'est pas
      dupliquée.
    """
    event_lines = list(event_lines)
    joined = "".join(event_lines)
    fragments = [n for n in notes if not (skip_if_present and n in joined)]
    if not fragments:
        return event_lines

    idx = _find_field_index(event_lines, "DESCRIPTION")
    current_value = event_lines[idx][len("DESCRIPTION:"):].rstrip() if idx is not None else ""

    if current_value:
        # Il y a déjà du texte : on enchaîne avec un \n avant chaque note.
        addition = "".join(f"\\n{note}" for note in fragments)
        event_lines[idx] = event_lines[idx].rstrip() + addition + "\n"
    else:
        # Champ absent OU présent mais vide : pas de \n parasite en tête.
        joined_fragments = "\\n".join(fragments)
        new_line = f"DESCRIPTION:{joined_fragments}\n"
        if idx is not None:
            event_lines[idx] = new_line
        else:
            event_lines.insert(len(event_lines) - 1, new_line)
    return event_lines


def _transform_vevents(text: str, transform: Callable[[list[str]], list[str]]) -> str:
    """Applique `transform` à chaque bloc VEVENT du contenu ICS ; le reste
    du document (en-tête, texte entre les événements) est recopié tel quel."""
    out_lines: list[str] = []
    event_lines: list[str] = []
    in_event = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            in_event = True
            event_lines = [line]
        elif stripped == "END:VEVENT" and in_event:
            event_lines.append(line)
            out_lines.extend(transform(event_lines))
            in_event = False
        elif in_event:
            event_lines.append(line)
        else:
            out_lines.append(line)
    return "".join(out_lines)


def add_scrap_note_to_ics(
    content_bytes: bytes,
    download_datetime_str: str,
    add_place_note: bool = True,
    add_scrap_note: bool = True,
) -> bytes:
    """
    Ajoute, dans la description de chaque VEVENT du contenu ICS :
      - le lieu de l'événement en redondance (si un champ LOCATION existe),
      - une note "Scrap le ...".
    Retourne le contenu modifié.
    """
    content_str = _decode_ics_bytes(content_bytes)
    content_str = content_str.replace("\r\n", "\n").replace("\r", "\n")

    if "BEGIN:VEVENT" not in content_str:
        return content_bytes  # Aucun événement trouvé, on ne touche à rien

    scrap_note = f"(Scrap le {download_datetime_str})"

    def transform(event_lines: list[str]) -> list[str]:
        notes = []
        location = _extract_field_value(event_lines, "LOCATION")
        if add_place_note and location:
            notes.append(f"Lieu : {location}")
        if add_scrap_note:
            notes.append(scrap_note)
        return _append_notes_to_description(event_lines, notes, skip_if_present=True)

    new_content = _transform_vevents(content_str, transform)
    # encodage propre en UTF-8 (pas de suppression de caractères)
    return new_content.replace("\n", "\r\n").encode("utf-8")


# ========== Download Logic ==========
def download_section(
    session: requests.Session, section: str, suffix: str, download_datetime_str: str
) -> None:
    """Télécharge et sauvegarde le fichier ICS pour une section donnée."""
    try:
        url = BASE_URL + suffix
        log.info("Téléchargement de la section %s depuis %s", section, url)
        response = session.get(url, timeout=25, allow_redirects=True)
        response.raise_for_status()
        if b"<!doctype html" in response.content.lower() and b"connexion" in response.content.lower():
            log.warning("%s -> Page de connexion reçue", section)
            return
        modified_content = add_scrap_note_to_ics(
            response.content,
            download_datetime_str,
            CONFIG.add_place_note,
            CONFIG.add_scrap_note,
        )
        dest_path = os.path.join(ICS_DIR, f"{section}.ics")
        with open(dest_path, "wb") as f:
            f.write(modified_content)
        log.info("Fichier ICS sauvegardé pour %s (%d octets)", section, len(modified_content))
    except Exception as e:
        log.error("Erreur lors du téléchargement de %s : %s", section, e)


def download_all_sections_once() -> None:
    """Télécharge toutes les sections une fois."""
    log.info("Début du cycle de téléchargement (connexion + toutes les sections).")
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        # Connexion
        session.get(CONFIG.login_url, timeout=15, allow_redirects=True)
        payload = {"Username": CONFIG.username, "Password": CONFIG.password, "url": "", "login": ""}
        response = session.post(CONFIG.login_url, data=payload, timeout=15, allow_redirects=True)
        if "<!doctype html" in response.text.lower() and "connexion" in response.text.lower():
            log.warning("Échec de la connexion, page de connexion reçue.")
            return
        log.info("Connexion réussie ; téléchargement de %d sections.", len(CONFIG.sections))
        download_datetime_str = get_current_datetime_str()
        for section, suffix in CONFIG.sections.items():
            download_section(session, section, suffix, download_datetime_str)
    except Exception as e:
        log.exception("Échec du cycle de connexion/téléchargement : %s", e)


def scheduler() -> None:
    """Planifie le téléchargement périodique des sections."""
    while True:
        download_all_sections_once()
        time.sleep(CONFIG.download_interval)


# ========== Flask App ==========
app = Flask(__name__)
Thread(target=scheduler, daemon=True).start()


@app.route("/")
def index():
    """Retourne la liste des sections disponibles."""
    return jsonify({"sections": list(CONFIG.sections.keys())})


@app.route("/admin/create_token", methods=["POST"])
def create_token():
    """Crée un nouveau token d'accès."""
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {CONFIG.admin_static_token}":
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "missing name"}), 400
    token = token_store.create(name)
    return jsonify({"name": name, "token": token}), 201


@app.route("/<section>")
def get_ics(section):
    """Retourne le fichier ICS pour une section donnée, en ajoutant à la
    volée une note "Importé le ..." dans chaque description si la variable d'environnement ADD_IMPORT_NOTE est activée."""
    token_value = request.args.get("token")
    if not token_value or not token_store.is_valid(token_value):
        return jsonify({"error": "invalid or missing token"}), 401

    matched_section = next((s for s in CONFIG.sections if s.lower() == section.lower()), None)
    if not matched_section:
        return jsonify({"error": f"section inconnue : {section}"}), 404

    ics_path = os.path.join(ICS_DIR, f"{matched_section}.ics")
    if not os.path.exists(ics_path):
        return jsonify({"error": f"fichier ICS pour {matched_section} non encore téléchargé"}), 503

    with open(ics_path, "r", encoding="utf-8") as f:
        content = f.read()

    if CONFIG.add_import_note:
        import_note = f"(Importé le {get_current_datetime_str()})"

        def transform(event_lines: list[str]) -> list[str]:
            return _append_notes_to_description(event_lines, [import_note], skip_if_present=False)

        ics_content = _transform_vevents(content, transform).replace("\n", "\r\n")
    else:
        ics_content = content.replace("\n", "\r\n")

    return Response(
        ics_content,
        mimetype="text/calendar",
        headers={"Content-Disposition": f"attachment; filename={matched_section}.ics"},
    )


if __name__ == "__main__":
    log.info("Sections disponibles : %s", list(CONFIG.sections.keys()))
    log.info("Démarrage de l'application sur %s:%d", CONFIG.flask_host, CONFIG.flask_port)
    app.run(host=CONFIG.flask_host, port=CONFIG.flask_port)
