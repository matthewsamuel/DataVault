"""
DataGatherer connector: Facebook ('Download your information', JSON)

The export is requested in the user's own browser in Meta's Accounts Center (no API for a
personal account, and never a bot or a stored password). The window shows the 'since' date
and puts it on the clipboard, so each request only asks for what is new; the overlap is
absorbed by the stable keys. Meta can also send the export on a schedule to Dropbox, Google
Drive or Koofr: then DataVault watches that synced folder (setting 'Gesynchroniseerde map').

Reads posts, comments and reactions (social_items, source 'facebook') and the Messenger
chats in the export (msg_* tables, source 'messenger', next to the encrypted chats of the
Messenger card). Text is repaired from Meta's mojibake; Dutch and English folder names work
because files are recognised by their content (formats/meta_dyi.py).
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import meta_import, messages_store
from backend.datagatherer.base import Connector, Coverage, ExportKind, GatherContext, HelpOption, SettingField
from backend.datagatherer.formats import meta_dyi

DYI_URL = "https://accountscenter.facebook.com/info_and_permissions/dyi/"
FB_MARKERS = ("profile_information/", "your_posts", "comments_and_reactions/", "your_facebook_activity/",
              "jouw_facebook_activiteit/", "je_facebook_activiteit/")


def is_instagram(path: Path, names: List[str]) -> bool:
    if path.name.lower().startswith("instagram"):
        return True
    for n in names[:5000]:
        low = n.lower()
        if "instagram" in low or low.startswith(("content/posts_", "media/posts/")) or "/content/posts_" in low:
            return True
    return False


def is_facebook(path: Path, names: List[str]) -> bool:
    if is_instagram(path, names) or not any(n.lower().endswith(".json") for n in names):
        return False
    if path.name.lower().startswith("facebook"):
        return True
    return any(any(m in n.lower() for m in FB_MARKERS) or (meta_dyi.MESSAGE_FILE.search(n) and meta_dyi.has_folder(n, "inbox"))
               for n in names[:5000])


class Facebook(Connector):
    id = "facebook"
    name = "Facebook"
    icon = "fab fa-facebook"
    category = "Sociaal"
    order = 10
    record_label = ("item", "items")
    description = "Je berichten, reacties, likes en Messenger-chats uit je Facebook-export."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Facebook"
    existing_folders = ("RAW_facebook",)
    folder_patterns = ("*.json",)
    stale_days = 30
    watch_setting = "watch_folder"
    exports = (
        ExportKind(
            id="dyi", label="Je gegevens exporteren (JSON)", request_url=DYI_URL, date_range=True,
            patterns=("facebook-*.zip", "*.zip"), folder_label="Facebook-export", expected_days=(0, 4),
            steps=(
                "Log in op de pagina die nu opent (Accountcentrum > Je gegevens en machtigingen > Je gegevens "
                "exporteren), in je eigen browser. DataVault ziet je wachtwoord nooit.",
                "Klik op Export maken, kies je Facebook-profiel en Exporteren naar apparaat.",
                "Kies bij Datumbereik 'Aangepast' en vul als begindatum de datum 'sinds' in die DataVault op je "
                "klembord zette.",
                "Kies Formaat JSON (niet HTML) en Mediakwaliteit Laag, en klik op Export starten.",
                "Meta laat weten wanneer de export klaar is (minuten tot dagen). Download de zip binnen 4 dagen.",
                "DataVault ziet de zip in Downloads, kopieert hem naar het archief en leest hem in. Het origineel mag "
                "je daarna weggooien.",
            )),
    )
    settings = (
        SettingField("my_name", "Je naam op Facebook", help="Zo herkent DataVault welke berichten van jou zijn. Leeg: "
                     "DataVault haalt hem uit je export.", placeholder="Voornaam Achternaam"),
        SettingField("watch_folder", "Gesynchroniseerde map", help="Optioneel: de map in Dropbox, Google Drive of Koofr "
                     "waar Meta je terugkerende export neerzet. DataVault kijkt daar altijd en leest nieuwe exports in.",
                     placeholder="/Users/jij/Dropbox/Meta"),
    )
    help = (
        HelpOption(
            title="Export aanvragen sinds je laatste import", effort="makkelijk",
            steps=("Klik op Export aanvragen: het Accountcentrum opent in je browser en de datum 'sinds' staat op je "
                   "klembord.",
                   "Kies JSON, het datumbereik vanaf die datum en lage mediakwaliteit.",
                   "Download de zip als Meta laat weten dat hij klaar is: DataVault doet de rest."),
            note="Doe één keer een volledige export (alles, JSON) als basis; daarna volstaat telkens een korte periode."),
        HelpOption(
            title="Terugkerende export naar je cloud", effort="gemiddeld",
            steps=("Kies in het Accountcentrum bij Je gegevens exporteren: Exporteren naar externe service.",
                   "Kies Dropbox, Google Drive of Koofr, JSON, lage mediakwaliteit en bijvoorbeeld wekelijks met het "
                   "bereik 'afgelopen week'.",
                   "Zorg dat die map op deze Mac gesynchroniseerd is, en vul hem in bij Instellingen > "
                   "Gesynchroniseerde map."),
            note="Je gegevens gaan dan langs je eigen cloud; DataVault zelf maakt geen verbinding."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        return "dyi" if names and is_facebook(path, names) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        return meta_import.import_export(ctx, raw, social_source="facebook", message_source="messenger",
                                         origin="facebook_dyi")

    def coverage(self, ctx: GatherContext) -> Coverage:
        return messages_store.coverage(ctx.db, source="messenger", origins=("facebook_dyi",), social_source="facebook")


CONNECTOR = Facebook()
