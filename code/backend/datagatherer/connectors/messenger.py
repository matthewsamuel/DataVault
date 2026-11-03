"""
DataGatherer connector: Messenger

Two parts, one source ('messenger' in the msg_* tables):
- Chats from before end-to-end encryption come with the Facebook export (connector
  facebook writes them here).
- End-to-end encrypted chats (the default since December 2023) are only in Messenger's
  'secure storage'. The user downloads them on messenger.com ('Download secure storage
  data'): messages.zip with one JSON file per chat (camelCase keys) and a media folder.
  This connector recognises that zip in Downloads and reads it.

A chat from the Facebook export is keyed by its folder id; the same chat in the secure
storage download is linked to it by its participants (meta_import.resolve_conversation),
and a message within a chat is the same when its time in ms, sender and text are, so
overlapping downloads and exports add nothing twice.
"""
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import meta_import, messages_store
from backend.datagatherer.base import Connector, Coverage, ExportKind, GatherContext, HelpOption, SettingField
from backend.datagatherer.connectors.facebook import is_facebook, is_instagram
from backend.datagatherer.formats import meta_dyi

MESSENGER_URL = "https://www.messenger.com/"
PEEK_BYTES = 2 * 1024 * 1024


def _peek_secure(path: Path, names: List[str]) -> bool:
    """True when the first chat file in the zip has the secure storage schema."""
    candidates = [n for n in names if n.lower().endswith(".json") and not n.startswith("__MACOSX/")][:3]
    try:
        with zipfile.ZipFile(path) as zf:
            for name in candidates:
                info = zf.getinfo(name)
                if info.file_size > PEEK_BYTES * 20:
                    continue
                with zf.open(info) as member:
                    head = member.read(PEEK_BYTES)
                if b'"senderName"' in head or b'"threadName"' in head or b'"isUnsent"' in head:
                    return True
                try:
                    if meta_dyi.classify(name, json.loads(head)) == "secure_thread":
                        return True
                except ValueError:
                    continue
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError):
        return False
    return False


class Messenger(Connector):
    id = "messenger"
    name = "Messenger"
    icon = "fab fa-facebook-messenger"
    category = "Berichten"
    order = 30
    record_label = ("bericht", "berichten")
    description = "Je Messenger-chats: de versleutelde uit je beveiligde opslag, aangevuld met de oudere uit je Facebook-export."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Messenger"
    existing_folders = ("RAW_messenger",)
    folder_patterns = ("*.json",)
    stale_days = 30
    holds_messages = True
    exports = (
        ExportKind(
            id="secure", label="Versleutelde chats (beveiligde opslag)", request_url=MESSENGER_URL,
            patterns=("messages*.zip", "*.zip"), folder_label="Beveiligde opslag", expected_days=(0, 1),
            steps=(
                "Log in op messenger.com in de pagina die nu opent, in je eigen browser (dit werkt het best in Chrome).",
                "Klik op je profielfoto > Privacy en veiligheid > End-to-end versleutelde chats > Berichtopslag.",
                "Klik op 'Gegevens uit beveiligde opslag downloaden' en dan op Bestand downloaden. Meta vraagt "
                "misschien je wachtwoord en je pincode van de beveiligde opslag.",
                "Laat de pagina open tot messages.zip gedownload is.",
                "DataVault ziet messages.zip in Downloads, kopieert hem naar het archief en leest hem in.",
            )),
    )
    settings = (
        SettingField("my_name", "Je naam in Messenger", help="Zo herkent DataVault welke berichten van jou zijn. Leeg: "
                     "DataVault gebruikt de naam uit je Facebook-export.", placeholder="Voornaam Achternaam"),
        SettingField("pasha", "Pasha mag deze berichten lezen", kind="toggle", help="Standaard uit. Aan: Pasha mag je "
                     "Messenger-berichten gebruiken om je vragen te beantwoorden, alleen op deze Mac."),
    )
    help = (
        HelpOption(
            title="Versleutelde chats downloaden", effort="makkelijk",
            steps=("Klik op Export aanvragen: messenger.com opent in je browser.",
                   "Ga naar Privacy en veiligheid > End-to-end versleutelde chats > Berichtopslag en download je "
                   "gegevens uit de beveiligde opslag.",
                   "DataVault leest messages.zip vanzelf in uit Downloads."),
            note="Daarvoor moet Beveiligde opslag aan staan (met een pincode). Er is geen schema: download hem af en "
                 "toe opnieuw; dubbele berichten worden overgeslagen."),
        HelpOption(
            title="Oudere chats via je Facebook-export", effort="gemiddeld",
            steps=("Vraag op de kaart van Facebook een export aan, met Berichten erin.",
                   "DataVault leest de chats daaruit in bij Messenger, naast je versleutelde chats."),
            note="Chats van vóór december 2023 staan meestal alleen in de Facebook-export."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if not names or is_instagram(path, names) or is_facebook(path, names):
            return None
        return "secure" if _peek_secure(path, names) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        return meta_import.import_export(ctx, raw, social_source=None, message_source="messenger",
                                         origin="messenger_e2ee", secure=True)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return messages_store.coverage(ctx.db, source="messenger")


CONNECTOR = Messenger()
