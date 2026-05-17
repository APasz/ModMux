from __future__ import annotations

import sys
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
from modmux.modmux_errors import NotFound, ProviderError
from modmux.providers.transportfevernet import TransportfevernetClient


def _repo_file(
    *,
    name: str,
    author: str,
    modid: str,
    version: str,
    download: str,
    download_size: int,
    utc_changed: int,
    entryurl: str,
) -> dict[str, object]:
    return {
        "name": name,
        "author": author,
        "modid": modid,
        "version": version,
        "download": download,
        "download_size": download_size,
        "utc_changed": utc_changed,
        "entryurl": entryurl,
    }


def _tpf2_files() -> list[dict[str, object]]:
    return [
        _repo_file(
            name="Cable Car",
            author="Marcolino26",
            modid="marc345_seilbahn_1",
            version="1.0",
            download="7867/?fileID=100",
            download_size=10,
            utc_changed=1_600_000_000,
            entryurl="7867/",
        ),
        _repo_file(
            name="Cable Car",
            author="Marcolino26",
            modid="marc345_seilbahn_1",
            version="1.1",
            download="7867/?fileID=101",
            download_size=20,
            utc_changed=1_700_000_000,
            entryurl="7867/",
        ),
        _repo_file(
            name="Cable Car",
            author="Marcolino26",
            modid="marc345_seilbahn_1",
            version="1.1",
            download="7867/?fileID=102",
            download_size=30,
            utc_changed=1_699_999_999,
            entryurl="7867/",
        ),
        _repo_file(
            name="Other Mod",
            author="Yoshi",
            modid="yoshi_other_1",
            version="1.0",
            download="7000/?fileID=200",
            download_size=40,
            utc_changed=1_650_000_000,
            entryurl="7000/",
        ),
    ]


def _repo_payload(
    *,
    complete: bool = True,
    name: str = "transportfever.net TPF2",
    files: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "repo": {
            "format": "CommonAPIRepo",
            "version": 1,
            "name": name,
            "download_prefix_url": "https://www.transportfever.net/filebase/entry-download/",
            "entry_prefix_url": "https://www.transportfever.net/filebase/entry/",
            "utc_time": 1_778_416_741,
            "complete": complete,
        },
        "files": _tpf2_files() if files is None else files,
    }


def _tpf1_repo_payload() -> dict[str, object]:
    return _repo_payload(
        name="transportfever.net TPF1",
        files=[
            _repo_file(
                name="Classic Station",
                author="Urbanist",
                modid="urbanist_classic_station_1",
                version="1.0",
                download="1234/?fileID=50",
                download_size=1000,
                utc_changed=1_500_000_000,
                entryurl="1234/",
            )
        ],
    )


def _empty_repo_payload() -> dict[str, object]:
    return _repo_payload(files=[])


def _json_handler(
    routes: Mapping[str, dict[str, object]],
    requested_paths: list[str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if requested_paths is not None:
            requested_paths.append(request.url.path)
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, json=payload, request=request)

    return handler


_TERMS_HTML = """
<!doctype html>
<html>
    <body id="tpl_filebase_termsOfUse" data-template="termsOfUse">
        <form method="post" action="https://www.transportfever.net/filebase/terms-of-use/">
            <input type="hidden" name="redirect" value="/filebase/entry/8010-smart-town-development/">
            <input type="hidden" name="t" value="terms-token">
        </form>
    </body>
</html>
"""


_ENTRY_HTML = """
<!doctype html>
<html>
<head>
    <title>Smart Town Development - Transport Fever Community</title>
    <meta name="description" content="Live page summary">
    <meta property="og:title" content="Smart Town Development - Transport Fever Community">
    <meta property="og:url" content="https://www.transportfever.net/filebase/entry/8010-smart-town-development/">
    <meta property="og:description" content="A newly uploaded mod before the repository refreshes.">
    <link rel="canonical" href="https://www.transportfever.net/filebase/entry/8010-smart-town-development/">
</head>
<body id="tpl_filebase_entry" data-template="entry">
    <header class="contentHeader filebaseEntryHeader">
        <h1 class="contentTitle"><span itemprop="name headline">Smart Town Development</span></h1>
        <ul>
            <li itemprop="author" itemscope itemtype="http://schema.org/Person">
                <a href="https://www.transportfever.net/wsc/user/99-example/" class="userLink" data-user-id="99">
                    <span itemprop="name">ExampleAuthor</span>
                </a>
            </li>
            <li>
                <meta itemprop="datePublished" content="2026-05-12T08:00:00+02:00">
            </li>
            <li>
                <meta itemprop="dateModified" content="2026-05-12T09:00:00+02:00">
            </li>
        </ul>
    </header>
    <article class="filebaseEntry message" data-object-id="8010">
        <section class="section">
            <h3 class="sectionTitle">Versionsinfos</h3>
            <dl>
                <dt>Aktuelle Version</dt>
                <dd class="htmlContent">1.0</dd>
            </dl>
        </section>
        <div class="filebaseFileList">
            <a href="https://www.transportfever.net/filebase/entry-download/8010-smart-town-development/?fileID=17001"
               title="Smart Town Development 1.0.zip">Smart Town Development 1.0.zip</a>
        </div>
    </article>
</body>
</html>
"""


class TestTransportfevernetClient(unittest.IsolatedAsyncioTestCase):
    def test_parse_url(self) -> None:
        parsed = TransportfevernetClient.parse_url("https://www.transportfever.net/filebase/entry/7867-cable-car/")
        self.assertEqual(parsed, ModID(provider=Provider.TRANSPORTFEVERNET, id="7867"))

        legacy = TransportfevernetClient.parse_url(
            "https://www.transportfever.net/filebase/index.php?entry/7867-cable-car/"
        )
        self.assertEqual(legacy, ModID(provider=Provider.TRANSPORTFEVERNET, id="7867"))

        encoded_legacy = TransportfevernetClient.parse_url(
            "https://www.transportfever.net/filebase/index.php?entry%2F6047-schallschutzw%C3%A4nde-von-spyos%2F%3A"
        )
        self.assertEqual(encoded_legacy, ModID(provider=Provider.TRANSPORTFEVERNET, id="6047"))

        self.assertIsNone(TransportfevernetClient.parse_url("https://example.com/filebase/entry/7867-cable-car/"))
        self.assertIsNone(TransportfevernetClient.parse_url("https://www.transportfever.net/filebase/"))

    async def test_get_mod_maps_entry_url_fields(self) -> None:
        requested_paths: list[str] = []

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                _json_handler({"/filebase/repos/tpf2.json": _repo_payload()}, requested_paths)
            )
        ) as http:
            client = TransportfevernetClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="7867"))

        self.assertEqual(requested_paths, ["/filebase/repos/tpf2.json"])
        self.assertEqual(mod.id.id, "7867")
        self.assertEqual(mod.slug, "marc345_seilbahn_1")
        self.assertEqual(mod.name.value, "Cable Car")
        self.assertEqual(mod.author.id, "Marcolino26")
        self.assertEqual(mod.author.name, "Marcolino26")
        self.assertEqual(str(mod.homepage), "https://www.transportfever.net/filebase/entry/7867/")
        self.assertEqual(mod.latest_version_id, "1.1")
        self.assertIsNotNone(mod.created_at)
        self.assertIsNotNone(mod.updated_at)
        assert mod.created_at is not None
        assert mod.updated_at is not None
        self.assertEqual(mod.created_at.year, 2020)
        self.assertEqual(mod.updated_at.year, 2023)
        self.assertIsNotNone(mod.latest_version)
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.version, "1.1")
        self.assertEqual([file.file_id for file in mod.latest_version.files], ["101", "102"])
        self.assertEqual(mod.latest_version.files[0].filename, "marc345_seilbahn_1_1.1_101")
        self.assertEqual(mod.latest_version.files[0].size_bytes, 20)
        self.assertEqual(
            mod.latest_version.raw["selected"]["download_url"],
            "https://www.transportfever.net/filebase/entry-download/7867/?fileID=101",
        )

    async def test_get_mod_accepts_internal_modid(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(_json_handler({"/filebase/repos/tpf2.json": _repo_payload()}))
        ) as http:
            client = TransportfevernetClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="marc345_seilbahn_1"))

        self.assertEqual(mod.id.id, "7867")
        self.assertEqual(mod.latest_version_id, "1.1")

    async def test_get_mod_uses_tpf1_repository_when_game_is_tpf1(self) -> None:
        requested_paths: list[str] = []

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                _json_handler({"/filebase/repos/tpf1.json": _tpf1_repo_payload()}, requested_paths)
            )
        ) as http:
            client = TransportfevernetClient(None, http=http)
            mod = await client.get_mod(
                ModID(provider=Provider.TRANSPORTFEVERNET, id="urbanist_classic_station_1", game="tpf1")
            )

        self.assertEqual(requested_paths, ["/filebase/repos/tpf1.json"])
        self.assertEqual(mod.id, ModID(provider=Provider.TRANSPORTFEVERNET, id="1234", game="tpf1"))
        self.assertEqual(mod.name.value, "Classic Station")
        self.assertEqual(mod.latest_version_id, "1.0")

    async def test_get_mod_normalises_game_aliases(self) -> None:
        requested_paths: list[str] = []

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                _json_handler(
                    {
                        "/filebase/repos/tpf1.json": _tpf1_repo_payload(),
                        "/filebase/repos/tpf2.json": _repo_payload(),
                    },
                    requested_paths,
                )
            )
        ) as http:
            client = TransportfevernetClient(None, http=http)
            mods = await client.get_mods(
                [
                    ModID(provider=Provider.TRANSPORTFEVERNET, id="urbanist_classic_station_1", game="transportfever1"),
                    ModID(provider=Provider.TRANSPORTFEVERNET, id="7867", game="tf2"),
                ]
            )

        self.assertEqual(requested_paths, ["/filebase/repos/tpf1.json", "/filebase/repos/tpf2.json"])
        self.assertEqual(mods[0].id.game, "tpf1")
        self.assertEqual(mods[1].id.game, "tpf2")

    async def test_get_mod_tries_tpf1_for_entry_id_when_default_repo_misses(self) -> None:
        requested_paths: list[str] = []

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                _json_handler(
                    {
                        "/filebase/repos/tpf2.json": _empty_repo_payload(),
                        "/filebase/repos/tpf1.json": _tpf1_repo_payload(),
                    },
                    requested_paths,
                )
            )
        ) as http:
            client = TransportfevernetClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="1234"))

        self.assertEqual(requested_paths, ["/filebase/repos/tpf2.json", "/filebase/repos/tpf1.json"])
        self.assertEqual(mod.id, ModID(provider=Provider.TRANSPORTFEVERNET, id="1234"))
        self.assertEqual(mod.name.value, "Classic Station")

    async def test_get_mod_rejects_unknown_game(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_json_handler({}))) as http:
            client = TransportfevernetClient(None, http=http)
            with self.assertRaises(ValueError):
                await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="1234", game="tpf3"))

    async def test_get_mod_rejects_incomplete_repository(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                _json_handler({"/filebase/repos/tpf2.json": _repo_payload(complete=False)})
            )
        ) as http:
            client = TransportfevernetClient(None, http=http)
            with self.assertRaises(ProviderError):
                await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="marc345_seilbahn_1"))

    async def test_get_mod_falls_back_to_html_for_entry_id_when_repository_is_incomplete(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/filebase/repos/tpf2.json":
                return httpx.Response(200, json=_repo_payload(complete=False), request=request)
            if request.url.path == "/filebase/repos/tpf1.json":
                return httpx.Response(200, json=_empty_repo_payload(), request=request)
            if request.url.path == "/filebase/entry/8010/":
                return httpx.Response(200, text=_ENTRY_HTML, request=request)
            return httpx.Response(404, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = TransportfevernetClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="8010"))

        self.assertEqual(mod.id.id, "8010")
        self.assertEqual(mod.raw["source"], "html_fallback")

    async def test_get_mod_raises_not_found(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(_json_handler({"/filebase/repos/tpf2.json": _repo_payload()}))
        ) as http:
            client = TransportfevernetClient(None, http=http)
            with self.assertRaises(NotFound):
                await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="missing_modid"))

    async def test_get_mod_falls_back_to_entry_html_when_repo_is_stale(self) -> None:
        accepted_terms = False
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal accepted_terms
            requested_paths.append(f"{request.method} {request.url.path}")
            if request.url.path == "/filebase/repos/tpf2.json":
                return httpx.Response(200, json=_empty_repo_payload(), request=request)
            if request.url.path in {
                "/filebase/entry/8010/",
                "/filebase/entry/8010-smart-town-development/",
            }:
                html = _ENTRY_HTML if accepted_terms else _TERMS_HTML
                return httpx.Response(200, text=html, request=request)
            if request.url.path == "/filebase/terms-of-use/" and request.method == "POST":
                self.assertEqual(
                    request.content.decode(),
                    "redirect=%2Ffilebase%2Fentry%2F8010-smart-town-development%2F&t=terms-token",
                )
                accepted_terms = True
                return httpx.Response(
                    302,
                    headers={"Location": "/filebase/entry/8010-smart-town-development/"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = TransportfevernetClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.TRANSPORTFEVERNET, id="8010"))

        self.assertEqual(
            requested_paths,
            [
                "GET /filebase/repos/tpf2.json",
                "GET /filebase/repos/tpf1.json",
                "GET /filebase/entry/8010/",
                "POST /filebase/terms-of-use/",
                "GET /filebase/entry/8010-smart-town-development/",
                "GET /filebase/entry/8010/",
            ],
        )
        self.assertEqual(mod.id.id, "8010")
        self.assertEqual(mod.name.value, "Smart Town Development")
        self.assertEqual(
            mod.description_md.value if mod.description_md else None,
            "A newly uploaded mod before the repository refreshes.",
        )
        self.assertEqual(mod.author.id, "99")
        self.assertEqual(mod.author.name, "ExampleAuthor")
        self.assertEqual(str(mod.homepage), "https://www.transportfever.net/filebase/entry/8010-smart-town-development/")
        self.assertEqual(mod.latest_version_id, "1.0")
        self.assertEqual(mod.raw["source"], "html_fallback")
        self.assertIsNotNone(mod.latest_version)
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.files[0].file_id, "17001")
        self.assertEqual(mod.latest_version.files[0].filename, "Smart Town Development 1.0.zip")
