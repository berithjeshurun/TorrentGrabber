"""Built-in search providers — mirrors BuiltinSearchProvidersModule.kt."""

from __future__ import annotations

from .anilibria import AniLibria
from .anirena import AniRena
from .animetosho import AnimeTosho
from .audiobookbay import AudioBookBay
from .bangumimoe import BangumiMoe
from .bitport import BitPort
from .bitsearch import BitSearch
from .blueroms import BlueRoms
from .bt4g import Bt4g
from .btdigg import BTDigg
from .btsow import Btsow
from .crotorrents import CROTorrents
from .dmhy import Dmhy
from .epublibre import EpubLibre
from .ext import Ext
from .eztv import Eztv
from .filemood import FileMood
from .fitgirlrepacks import FitGirlRepacks
from .glotorrents import GloTorrents
from .idope import IDope
from .igggames import IGGGames
from .internetarchive import InternetArchive
from .kickasstorrents import KickassTorrents
from .knaben import Knaben
from .limetorrents import LimeTorrents
from .linuxtracker import LinuxTracker
from .magnetdl import MagnetDL
from .megapeer import MegaPeer
from .mikan import Mikan
from .mypornclub import MyPornClub
from .nekobt import NekoBT
from .nonameclub import NoNameClub
from .nyaa import Nyaa
from .oxtorrent import OxTorrent
from .private_trackers import IPTorrents, MyAnonaMouse, TorrentLeech
from .publicdomaintorrents import PublicDomainTorrents
from .rutor import Rutor
from .rutracker import RuTracker
from .solidtorrents import SolidTorrents
from .subsplease import SubsPlease
from .sukebei import Sukebei
from .thepiratebay import ThePirateBay
from .therarbg import TheRarBg
from .thirteenthirtysevenx import ThirteenThirtySevenX
from .tokyotoshokan import TokyoToshokan
from .torlock import Torlock
from .torrent9 import Torrent9
from .torrentdatabase import TorrentDatabase
from .torrentdownload import TorrentDownload
from .torrentdownloads import TorrentDownloads
from .torrentgalaxy import TorrentGalaxy
from .torrentkitty import TorrentKitty
from .torrentproject import TorrentProject
from .torrends import Torrends
from .torrentscsv import TorrentsCSV
from .torrentz import Torrentz
from .uindex import UIndex
from .xxxclub import XXXClub
from .xxxtracker import XXXTracker
from .yourbittorrent import YourBittorrent
from .yts import Yts
from .zeromagnet import ZeroMagnet

# Torznab is config-driven (not instantiated in the default builtin list).
from .torznab import TorznabSearchProvider  # noqa: F401

BUILTIN_PROVIDER_CLASSES = [
    AniLibria,
    AniRena,
    AnimeTosho,
    AudioBookBay,
    BTDigg,
    BangumiMoe,
    BitPort,
    BitSearch,
    BlueRoms,
    Bt4g,
    Btsow,
    CROTorrents,
    Dmhy,
    EpubLibre,
    Ext,
    Eztv,
    FileMood,
    FitGirlRepacks,
    GloTorrents,
    IDope,
    IGGGames,
    IPTorrents,
    InternetArchive,
    KickassTorrents,
    Knaben,
    LimeTorrents,
    LinuxTracker,
    MagnetDL,
    MegaPeer,
    Mikan,
    MyAnonaMouse,
    MyPornClub,
    NekoBT,
    NoNameClub,
    Nyaa,
    OxTorrent,
    PublicDomainTorrents,
    RuTracker,
    Rutor,
    SolidTorrents,
    SubsPlease,
    Sukebei,
    ThePirateBay,
    TheRarBg,
    ThirteenThirtySevenX,
    TokyoToshokan,
    Torlock,
    Torrent9,
    TorrentDatabase,
    TorrentDownload,
    TorrentDownloads,
    TorrentGalaxy,
    TorrentKitty,
    TorrentLeech,
    TorrentProject,
    Torrends,
    TorrentsCSV,
    Torrentz,
    UIndex,
    XXXClub,
    XXXTracker,
    YourBittorrent,
    Yts,
    ZeroMagnet,
]

__all__ = [
    "BUILTIN_PROVIDER_CLASSES",
    "TorznabSearchProvider",
    *[cls.__name__ for cls in BUILTIN_PROVIDER_CLASSES],
]
