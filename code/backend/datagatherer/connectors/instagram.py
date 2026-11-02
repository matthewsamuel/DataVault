"""
DataGatherer connector: Instagram ('Download your information', JSON)

Like Facebook: requested in Accounts Center in the user's own browser, with the 'since'
date on the clipboard, or sent by Meta on a schedule to a synced cloud folder. Reads posts,
stories and reels with their metadata (EXIF where Instagram kept it), likes and comments
(social_items, source 'instagram') and the direct messages (source 'instagram'; since
8 May 2026 Instagram no longer encrypts them, so they are all in the export).
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import meta_import, messages_store
from backend.datagatherer.base import Connector, Coverage, ExportKind, GatherContext, HelpOption, SettingField
from backend.datagatherer.connectors.facebook import is_instagram, meta_account

DYI_URL = "https://accountscenter.instagram.com/info_and_permissions/dyi/"


class Instagram(Connector):
    id = "instagram"
    name = "Instagram"
    icon = "fab fa-instagram"
    category = "Sociaal"
    order = 20
    record_label = ("item", "items")
    description = "Je foto's en video's met hun gegevens, likes, reacties en directe berichten uit je Instagram-export."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Instagram"
    existing_folders = ("RAW_instagram",)
    folder_patterns = ("*.json",)
    stale_days = 30
    holds_messages = True
    watch_setting = "watch_folder"
    exports = (
        ExportKind(
            id="dyi", label="Je gegevens exporteren (JSON)", request_url=DYI_URL, date_range=True,
            patterns=("instagram-*.zip", "*.zip"), folder_label="Instagram-export", expected_days=(0, 14),
            steps=(
                "Log in op de pagina die nu opent (Accountcentrum > Je gegevens en machtigingen > Je gegevens "
                "exporteren), in je eigen browser. DataVault ziet je wachtwoord nooit.",
                "Klik op Export maken, kies je Instagram-account en Exporteren naar apparaat.",
                "Kies bij Datumbereik 'Aangepast' en vul als begindatum de datum 'sinds' in die DataVault op je "
                "klembord zette.",
                "Kies Formaat JSON (niet HTML) en Mediakwaliteit Laag, en klik op Export starten.",
                "Instagram laat weten wanneer de export klaar is (meestal binnen een dag, soms tot 14 dagen). Download "
                "de zip binnen 4 dagen.",
                "DataVault ziet de zip in Downloads, kopieert hem naar het archief en leest hem in.",
            )),
    )
    settings = (
        SettingField("my_name", "Je naam op Instagram", help="Je weergavenaam in berichten. Leeg: DataVault haalt hem uit "
                     "je export.", placeholder="Voornaam Achternaam"),
        SettingField("watch_folder", "Gesynchroniseerde map", help="Optioneel: de map in Dropbox, Google Drive of Koofr "
                     "waar Meta je terugkerende export neerzet.", placeholder="/Users/jij/Dropbox/Meta"),
        SettingField("pasha", "Pasha mag deze berichten lezen", kind="toggle", help="Standaard uit. Aan: Pasha mag je "
                     "Instagram-berichten gebruiken om je vragen te beantwoorden, alleen op deze Mac."),
    )
    help = (
        HelpOption(
            title="Export aanvragen sinds je laatste import", effort="makkelijk",
            steps=("Klik op Export aanvragen: het Accountcentrum opent en de datum 'sinds' staat op je klembord.",
                   "Kies JSON, het datumbereik vanaf die datum en lage mediakwaliteit.",
                   "Download de zip als Instagram laat weten dat hij klaar is: DataVault doet de rest."),
            note="Doe één keer een volledige export (alles, JSON, hoge mediakwaliteit) als basis."),
        HelpOption(
            title="Terugkerende export naar je cloud", effort="gemiddeld",
            steps=("Kies in het Accountcentrum: Exporteren naar externe service (Dropbox, Google Drive of Koofr).",
                   "Kies JSON, lage mediakwaliteit en bijvoorbeeld wekelijks met het bereik 'afgelopen week'.",
                   "Vul de gesynchroniseerde map in bij Instellingen."),
            note="Oude versleutelde chats van vóór 8 mei 2026 staan alleen in een download die je toen zelf maakte."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names and any(n.lower().endswith(".json") for n in names) and is_instagram(path, names):
            return "dyi"
        return None

    def account_of(self, raw: Dict[str, Any]) -> Optional[str]:
        return meta_account(raw)

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        return meta_import.import_export(ctx, raw, social_source="instagram", message_source="instagram",
                                         origin="instagram_dyi")

    def coverage(self, ctx: GatherContext) -> Coverage:
        return messages_store.coverage(ctx.db, source="instagram", social_source="instagram")


CONNECTOR = Instagram()
