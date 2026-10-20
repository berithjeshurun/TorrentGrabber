"""NoNameClub search provider — port of NoNameClub.kt."""

from __future__ import annotations

from typing import Optional

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetRequiresFetch,
    SearchProviderSafety,
    Torrent,
    TorrentDetails,
)
from ..network import NetworkClient
from ..provider import (
    LatestTorrentsProvider,
    MagnetUriProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)

# Form param values
CRITERIA_REGISTERED = 1
CRITERIA_TOPIC_TITLE = 2
CRITERIA_SEEDERS = 10
ORDER_DESC = 2
DATE_ALL_TIME = -1
DATE_TODAY = 1

LIST_ITEM = (
    'table.forumline.tablesorter > tbody > tr:has(a[href^="viewtopic.php?t="])'
    ':has(a[href^="download.php?id="])'
)
TORRENT_NAME = 'a[href^="viewtopic.php?t="] > b'
SIZE = "td:nth-child(6) > u"
SEEDERS = "td.seedmed > b"
PEERS = "td.leechmed > b"
UPLOAD_DATE = "td:last-child > u"
CATEGORY = 'a[href^="tracker.php?f="]'
FILE_DOWNLOAD_LINK = 'a[href^="download.php?id="]'
DETAILS_PAGE_URL = 'a[href^="viewtopic.php?t="]'

_D_TORRENT_NAME = "a.maintitle"
_D_CATEGORY = "span.nav > a.nav:nth-child(2)"
_D_UPLOADER = "span.genmed > b"
_D_DESCRIPTION = "div.postbody"
_D_POSTER_IMAGE = "var.postImg"
_D_SCREENSHOT = "a.highslide"
_D_MAGNET_URI = 'a[href^="magnet:?xt="]'


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _to_long(value: Optional[str]) -> Optional[int]:
    return _to_int(value)


def _drop_last_while_not_ws(s: str) -> str:
    while s and not s[-1].isspace():
        s = s[:-1]
    return s


def _find_td_contains_own(root, text: str):
    for el in root.find_all("td"):
        own = "".join(el.find_all(string=True, recursive=False))
        if text in own:
            return el
    return None


def _get_category_from_id(cid: str) -> Category:
    # Ids from Jackett noname-club.yml
    if cid in {"48", "925", "872"}:
        return Category.OTHER

    if cid in {
        "724", "725", "729", "731", "1345", "733", "1346", "1329", "1330",
        "1331", "1332", "1340", "658", "890", "1336", "1337", "1338", "1339",
        "660", "232", "734", "742", "735", "738", "967", "907", "739", "1109",
        "736", "737", "898", "935", "871", "973", "960", "1239", "740", "741",
    }:
        return Category.SERIES

    if cid in {
        "503", "504", "506", "763", "1335", "1241", "1023", "717", "509", "508",
        "510", "1254", "1042", "511", "916", "512", "561", "1284", "562", "513",
        "514", "515", "516", "517", "518", "519", "520", "521", "522", "523",
        "524", "532", "533", "535", "530", "529", "525", "526", "527", "545",
        "764", "765", "820", "552", "553", "554", "550", "549", "548",
    }:
        return Category.APPS

    if cid in {
        "216", "270", "218", "219", "954", "217", "1293", "1298", "318", "320",
        "677", "1177", "319", "678", "885", "908", "1310", "909", "910", "911",
        "912", "220", "221", "222", "882", "889", "224", "225", "226", "227",
        "1296", "891", "1299", "682", "694", "884", "1211", "693", "913", "228",
        "1150", "1311", "1313", "1312", "256", "257", "258", "883", "955", "905",
        "271", "1210", "264", "265", "272", "1262", "266", "1294",
    }:
        return Category.MOVIES

    if cid in {
        "1219", "1221", "1220", "722", "768", "1344", "779", "1288", "787", "1141",
        "777", "786", "776", "785", "775", "1265", "1242", "1140", "782", "773",
        "1142", "772", "771", "783", "1144", "804", "1290", "1300", "784", "774",
        "922", "770", "1320", "780", "781", "1322", "769", "799", "800", "791",
        "793", "794", "796", "795", "713", "706", "577", "894", "578", "580",
        "579", "953", "581", "806", "714", "761", "809", "924", "812", "576",
        "590", "591", "588", "589", "598", "652", "599", "959", "956", "597",
        "593", "594", "819", "595", "587", "584", "586", "585", "600", "596",
        "1295", "614", "603", "1308", "1309", "1206", "1194", "1062", "974",
        "609", "1263", "951", "975", "608", "607", "606", "750", "605", "604",
        "950", "610", "613", "612", "653", "654", "611", "656", "437", "466",
        "1319", "463", "958", "1223", "467", "464", "465", "1348", "469",
    }:
        return Category.SERIES

    if cid in {
        "615", "616", "617", "648", "619", "620", "623", "622", "621", "632",
        "624", "627", "626", "625", "644", "628", "635", "634", "638", "646",
        "645", "639", "640",
    }:
        return Category.ANIME

    if cid in {
        "432", "755", "481", "557", "442", "441", "875", "1176", "444", "443",
        "440", "1199", "433", "447", "445", "817", "818", "434", "1349", "957",
        "931", "1152", "455", "453", "1063", "452", "451", "449", "1153", "1347",
        "482", "483", "484", "1343", "438", "485", "473", "472", "471", "895",
        "470", "896", "480", "439", "477", "476", "475", "474", "886", "478",
        "486", "490", "657", "489", "488", "487", "1198", "1227", "893", "491",
        "767", "299", "887", "301", "1334", "300", "1341", "492", "558", "1173",
        "1174", "1171", "662", "1175", "1172", "933", "815", "1170", "398", "816",
    }:
        return Category.BOOKS

    if cid in {
        "313", "1291", "680", "1149", "429", "681", "330", "1256", "1285", "370",
        "1260", "371", "1261", "375", "1259", "374", "1257", "373", "1258", "372",
        "1160", "876", "1255", "376", "326", "1352", "359", "358", "1353", "1188",
        "1189", "328", "1370", "1180", "1181", "364", "363", "1179", "879", "322",
        "1350", "962", "333", "1356", "965", "336", "1362", "337", "338", "1351",
        "963", "334", "1357", "961", "332", "325", "1354", "1355", "1165", "1166",
        "1168", "1167", "1162", "352", "1164", "1163", "1161", "353", "324", "1327",
        "1328", "1325", "1326", "1365", "1366", "1323", "1324", "976", "346", "1363",
        "977", "345", "349", "1243", "347", "979", "673", "671", "1224", "1225",
        "1367", "980", "672", "1316", "1317", "1364", "981", "344", "1368", "983",
        "984", "982", "348", "674", "323", "1369", "1187", "339", "1186", "340",
        "1185", "341", "329", "1361", "369", "368", "1218", "365", "1217", "366",
        "1215", "1216", "1213", "367", "331", "1358", "1157", "711", "1159", "378",
        "1359", "1158", "379", "380", "1178", "1360", "361", "360", "327", "1184",
        "824", "1182", "354", "877", "1183", "1190", "917", "1096", "1097", "1095",
    }:
        return Category.MUSIC

    if cid in {
        "410", "411", "412", "1008", "415", "746", "428", "1009", "413", "414",
        "1010", "1012", "1014", "416", "1013", "1015", "268", "1016", "1041",
        "1018", "1017", "972", "971", "970", "969", "968", "1146", "418", "1061",
        "1060", "1059", "1058", "1057", "1056", "1054", "1053", "1052", "1051",
        "1050", "1049", "1048", "1047", "1046", "1045", "1044", "382", "390",
        "387", "388", "1264", "1318", "385", "386", "848", "1321", "383", "384",
        "1292", "389", "391", "417", "1193", "1192",
    }:
        return Category.GAMES

    return Category.OTHER


class NoNameClub(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "nonameclub"
    name = "NoNameClub"
    url = "https://nnmclub.to"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_to_id = {
            Category.ANIME: 615,
            Category.APPS: 512,
            Category.BOOKS: 434,
            Category.GAMES: 410,
            Category.MOVIES: 216,
            Category.MUSIC: 313,
            Category.SERIES: 768,
            Category.OTHER: 48,
        }
        self._results_parser = _NnmClubResultsPageParser(self.id, self.name)

    def _build_request_form_payload(
        self,
        query: Optional[str],
        category: Category,
        sort_criteria: int,
        date_range: int,
    ) -> dict[str, str]:
        cat_id = self._category_to_id.get(category)
        return {
            "f[]": str(cat_id) if cat_id is not None else "-1",
            "o": str(sort_criteria),
            "s": str(ORDER_DESC),
            "tm": str(date_range),
            "shf": "1",
            "ta": "-1",
            "sns": "-1",
            "sds": "4",
            "nm": query or "",
            "submit": "Поиск",
        }

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/forum/tracker.php"
        form_data = self._build_request_form_payload(
            query=query,
            category=category,
            sort_criteria=CRITERIA_TOPIC_TITLE,
            date_range=DATE_ALL_TIME,
        )
        response_html = await self.network.submit_form(url=request_url, form_data=form_data)
        return self._results_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _NnmClubDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        details_page_html = await self.network.get_text(details_page_url)
        return _NnmClubDetailsPageParser.parse(details_page_html)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/forum/tracker.php"
        request_payload = self._build_request_form_payload(
            query=None,
            category=category,
            sort_criteria=CRITERIA_REGISTERED,
            date_range=DATE_TODAY,
        )
        response_html = await self.network.submit_form(request_url, request_payload)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/forum/tracker.php"
        request_payload = self._build_request_form_payload(
            query=None,
            category=category,
            sort_criteria=CRITERIA_SEEDERS,
            date_range=DATE_ALL_TIME,
        )
        response_html = await self.network.submit_form(request_url, request_payload)
        return self._results_parser.parse(response_html, request_url)


class _NnmClubResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self.provider_id = provider_id
        self.provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        dom = H.parse_html(html, page_url)
        return [
            t
            for item in H.select(dom, LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        details_page_url = H.attr(
            H.select_first(list_item, DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None

        torrent_remote_id = details_page_url.rsplit("=", 1)[-1]
        if not torrent_remote_id.strip():
            torrent_remote_id = None
        torrent_id = utils.create_torrent_id(
            self.provider_id, torrent_remote_id or details_page_url
        )

        torrent_name = H.own_text(H.select_first(list_item, TORRENT_NAME))
        if not torrent_name:
            return None

        size = None
        size_raw = H.own_text(H.select_first(list_item, SIZE))
        if size_raw:
            size = utils.format_bytes(size_raw)

        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))

        upload_date = None
        upload_raw = H.own_text(H.select_first(list_item, UPLOAD_DATE))
        epoch = _to_long(upload_raw)
        if epoch is not None:
            upload_date = utils.epoch_second_to_datetime(epoch)

        category = None
        href = H.attr(H.select_first(list_item, CATEGORY), "href") or ""
        if href:
            raw = href.removeprefix("tracker.php?f=")
            cid = ""
            for ch in raw:
                if ch == "&":
                    break
                cid += ch
            if cid:
                category = _get_category_from_id(cid)

        file_download_link = H.attr(
            H.select_first(list_item, FILE_DOWNLOAD_LINK), "abs:href", page_url
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            provider_name=self.provider_name,
            magnet_uri=MagnetRequiresFetch(details_page_url),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


class _NnmClubDetailsPageParser:
    @staticmethod
    def parse(details_page_html: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(details_page_html)

        torrent_name = H.own_text(H.select_first(dom, _D_TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
        if not magnet_uri:
            return None

        size = None
        size_label = _find_td_contains_own(dom, "Размер:")
        if size_label is not None:
            sib = size_label.find_next_sibling()
            if sib is not None:
                span = H.select_first(sib, "span:nth-child(1)")
                raw = H.own_text(span)
                if raw:
                    size = raw.strip()

        upload_date = None
        upload_label = _find_td_contains_own(dom, "Зарегистрирован:")
        if upload_label is not None:
            sib = upload_label.find_next_sibling()
            if sib is not None:
                raw = (H.own_text(sib) or "").strip()
                raw = _drop_last_while_not_ws(raw).strip()
                if raw:
                    try:
                        english = utils.convert_russian_month_to_english(raw)
                        upload_date = utils.parse_date(english, "dd MMM yyyy")
                    except (KeyError, ValueError):
                        pass

        last_checked = None
        checked_label = _find_td_contains_own(dom, "Проверка:")
        if checked_label is not None:
            sib = checked_label.find_next_sibling()
            if sib is not None:
                raw = (H.own_text(sib) or "").strip()
                raw = raw.removeprefix("Оформление проверено модератором").strip()
                raw = _drop_last_while_not_ws(raw).strip()
                if raw:
                    try:
                        english = utils.convert_russian_month_to_english(raw)
                        last_checked = utils.parse_date(english, "dd MMM yyyy")
                    except (KeyError, ValueError):
                        pass

        category = None
        href = H.attr(H.select_first(dom, _D_CATEGORY), "href") or ""
        if href:
            # takeLastWhile { it != '=' }
            cid = ""
            for ch in reversed(href):
                if ch == "=":
                    break
                cid = ch + cid
            if cid:
                category = _get_category_from_id(cid)

        uploader = H.own_text(H.select_first(dom, _D_UPLOADER))
        description_el = H.select_first(dom, _D_DESCRIPTION)
        description = description_el.decode_contents() if description_el else None
        poster_image_url = H.attr(H.select_first(dom, _D_POSTER_IMAGE), "title")
        screenshot_urls = [
            href
            for el in H.select(dom, _D_SCREENSHOT)
            if (href := H.attr(el, "href"))
        ]

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            last_checked=last_checked,
            category=category,
            uploader=uploader,
            description=description,
            magnet_uri=magnet_uri,
            poster_url=poster_image_url,
            screenshot_urls=screenshot_urls,
        )

    @staticmethod
    def extract_magnet_uri(details_page_html: str) -> Optional[str]:
        dom = H.parse_html(details_page_html)
        return H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
