from __future__ import annotations

import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux import cli
from modmux.models import ModID, Provider
from modmux.modmux_errors import BatchResponseError


class _FakeMod:
    def __init__(self, name: str = "demo") -> None:
        self.name = name

    def model_dump_json(self, *, indent: int | None = None) -> str:
        if indent is None:
            return f'{{"name":"{self.name}"}}'
        return f'{{\n  "name": "{self.name}"\n}}'

    def model_dump(self, *, mode: str = "python") -> dict[str, str]:
        assert mode == "json"
        return {"name": self.name}


class _FakeMuxer:
    last_instance: _FakeMuxer | None = None

    def __init__(self, *, creds: object = None) -> None:
        self.creds = creds
        self.get_mod = AsyncMock(side_effect=self._get_mod_impl)
        self.get_mods = AsyncMock(side_effect=self._get_mods_impl)
        _FakeMuxer.last_instance = self

    async def _get_mod_impl(self, provider: Provider, mod_id: ModID) -> _FakeMod:
        return _FakeMod(name=f"{provider.value}:{mod_id.id}")

    async def _get_mods_impl(self, provider: Provider, mod_ids: cli.Sequence[ModID]) -> list[_FakeMod]:
        return [_FakeMod(name=f"{provider.value}:{mod_id.id}") for mod_id in mod_ids]

    async def __aenter__(self) -> _FakeMuxer:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class TestCli(unittest.IsolatedAsyncioTestCase):
    async def test_run_uses_env_creds_and_pretty_output(self) -> None:
        output = io.StringIO()
        with (
            patch.object(cli, "Muxer", _FakeMuxer),
            patch.object(cli, "_load_dotenv", return_value=None),
            patch.dict(
                os.environ,
                {"MODMUX_STEAM_TOKEN": "steam-token", "MODMUX_STEAM_USER": "steam-user"},
                clear=False,
            ),
            redirect_stdout(output),
        ):
            result = await cli._run(["steam", "123", "--pretty"])

        self.assertEqual(result, 0)
        muxer = _FakeMuxer.last_instance
        self.assertIsNotNone(muxer)
        assert muxer is not None
        self.assertEqual(muxer.creds, {Provider.STEAM: {"token": "steam-token", "user": "steam-user"}})
        muxer.get_mod.assert_awaited_once_with(Provider.STEAM, ModID(provider=Provider.STEAM, id="123", game=None))
        self.assertEqual(output.getvalue(), '{\n  "name": "STEAM:123"\n}\n')

    async def test_run_without_creds_uses_none(self) -> None:
        output = io.StringIO()
        with (
            patch.object(cli, "Muxer", _FakeMuxer),
            patch.object(cli, "_load_dotenv", return_value=None),
            patch.dict(os.environ, {}, clear=True),
            redirect_stdout(output),
        ):
            result = await cli._run(["modrinth", "fabric-api"])

        self.assertEqual(result, 0)
        muxer = _FakeMuxer.last_instance
        self.assertIsNotNone(muxer)
        assert muxer is not None
        self.assertIsNone(muxer.creds)
        muxer.get_mod.assert_awaited_once_with(
            Provider.MODRINTH,
            ModID(provider=Provider.MODRINTH, id="fabric-api", game=None),
        )
        self.assertEqual(output.getvalue(), '{"name":"MODRINTH:fabric-api"}\n')

    async def test_run_bulk_mods_uses_get_mods_and_prints_json_array(self) -> None:
        output = io.StringIO()
        with (
            patch.object(cli, "Muxer", _FakeMuxer),
            patch.object(cli, "_load_dotenv", return_value=None),
            patch.dict(os.environ, {}, clear=True),
            redirect_stdout(output),
        ):
            result = await cli._run(["steam", "123", "456", "--pretty"])

        self.assertEqual(result, 0)
        muxer = _FakeMuxer.last_instance
        self.assertIsNotNone(muxer)
        assert muxer is not None
        muxer.get_mod.assert_not_called()
        muxer.get_mods.assert_awaited_once_with(
            Provider.STEAM,
            [
                ModID(provider=Provider.STEAM, id="123", game=None),
                ModID(provider=Provider.STEAM, id="456", game=None),
            ],
        )
        self.assertEqual(
            json.loads(output.getvalue()),
            [{"name": "STEAM:123"}, {"name": "STEAM:456"}],
        )

    async def test_run_from_urls_groups_by_provider_and_preserves_order(self) -> None:
        output = io.StringIO()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("# comment\n")
            handle.write("https://modrinth.com/mod/fabric-api\n")
            handle.write("\n")
            handle.write("https://steamcommunity.com/sharedfiles/filedetails/?id=12345&appid=480\n")
            path = handle.name

        try:
            with (
                patch.object(cli, "Muxer", _FakeMuxer),
                patch.object(cli, "_load_dotenv", return_value=None),
                patch.dict(os.environ, {}, clear=True),
                redirect_stdout(output),
            ):
                result = await cli._run(["--from-urls", path, "--pretty"])
        finally:
            os.unlink(path)

        self.assertEqual(result, 0)
        muxer = _FakeMuxer.last_instance
        self.assertIsNotNone(muxer)
        assert muxer is not None
        self.assertIsNone(muxer.creds)
        self.assertEqual(muxer.get_mod.await_count, 0)
        self.assertEqual(muxer.get_mods.await_count, 2)
        self.assertEqual(
            muxer.get_mods.await_args_list[0].args,
            (Provider.MODRINTH, [ModID(provider=Provider.MODRINTH, id="fabric-api", game=None)]),
        )
        self.assertEqual(
            muxer.get_mods.await_args_list[1].args,
            (Provider.STEAM, [ModID(provider=Provider.STEAM, id="12345", game="480")]),
        )
        self.assertEqual(
            json.loads(output.getvalue()),
            [{"name": "MODRINTH:fabric-api"}, {"name": "STEAM:12345"}],
        )

    async def test_run_from_urls_builds_provider_specific_creds(self) -> None:
        output = io.StringIO()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("https://mod.io/g/4321/m/some-mod\n")
            handle.write("https://modrinth.com/mod/fabric-api\n")
            path = handle.name

        try:
            with (
                patch.object(cli, "Muxer", _FakeMuxer),
                patch.object(cli, "_load_dotenv", return_value=None),
                patch.dict(
                    os.environ,
                    {"MODMUX_MODIO_TOKEN": "modio-token", "MODMUX_MODIO_USER": "modio-user"},
                    clear=True,
                ),
                redirect_stdout(output),
            ):
                result = await cli._run(["--from-urls", path])
        finally:
            os.unlink(path)

        self.assertEqual(result, 0)
        muxer = _FakeMuxer.last_instance
        self.assertIsNotNone(muxer)
        assert muxer is not None
        self.assertEqual(muxer.creds, {Provider.MODIO: {"token": "modio-token", "user": "modio-user"}})

    async def test_run_from_urls_raises_batch_response_error_for_short_provider_result(self) -> None:
        output = io.StringIO()

        class _ShortMuxer(_FakeMuxer):
            async def _get_mods_impl(self, provider: Provider, mod_ids: cli.Sequence[ModID]) -> list[_FakeMod]:
                mods = await super()._get_mods_impl(provider, mod_ids)
                return mods[:-1]

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("https://steamcommunity.com/sharedfiles/filedetails/?id=12345&appid=480\n")
            handle.write("https://steamcommunity.com/sharedfiles/filedetails/?id=67890&appid=480\n")
            path = handle.name

        try:
            with (
                patch.object(cli, "Muxer", _ShortMuxer),
                patch.object(cli, "_load_dotenv", return_value=None),
                patch.dict(os.environ, {}, clear=True),
                redirect_stdout(output),
            ):
                with self.assertRaises(BatchResponseError):
                    await cli._run(["--from-urls", path])
        finally:
            os.unlink(path)

    async def test_run_loads_creds_from_dotenv_file(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            urls_path = temp_path / "urls.txt"
            env_path = temp_path / ".env"
            urls_path.write_text("https://mod.io/g/4321/m/some-mod\n", encoding="utf-8")
            env_path.write_text(
                "MODMUX_MODIO_TOKEN=modio-token\nMODMUX_MODIO_USER=modio-user\n",
                encoding="utf-8",
            )

            previous_cwd = os.getcwd()
            os.chdir(temp_path)
            try:
                with (
                    patch.object(cli, "Muxer", _FakeMuxer),
                    patch.dict(os.environ, {}, clear=True),
                    redirect_stdout(output),
                ):
                    result = await cli._run(["--from-urls", str(urls_path)])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result, 0)
        muxer = _FakeMuxer.last_instance
        self.assertIsNotNone(muxer)
        assert muxer is not None
        self.assertEqual(muxer.creds, {Provider.MODIO: {"token": "modio-token", "user": "modio-user"}})

    async def test_run_loads_creds_from_repo_root_when_invoked_elsewhere(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as work_dir:
            repo_path = Path(repo_dir)
            work_path = Path(work_dir)
            urls_path = work_path / "urls.txt"
            env_path = repo_path / ".env"
            urls_path.write_text("https://mod.io/g/4321/m/some-mod\n", encoding="utf-8")
            env_path.write_text(
                "MODMUX_MODIO_TOKEN=repo-token\nMODMUX_MODIO_USER=repo-user\n",
                encoding="utf-8",
            )

            previous_cwd = os.getcwd()
            os.chdir(work_path)
            try:
                with (
                    patch.object(cli, "Muxer", _FakeMuxer),
                    patch.object(cli, "_REPO_ROOT", repo_path),
                    patch.dict(os.environ, {}, clear=True),
                    redirect_stdout(output),
                ):
                    result = await cli._run(["--from-urls", str(urls_path)])
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(result, 0)
        muxer = _FakeMuxer.last_instance
        self.assertIsNotNone(muxer)
        assert muxer is not None
        self.assertEqual(muxer.creds, {Provider.MODIO: {"token": "repo-token", "user": "repo-user"}})

    def test_resolve_dotenv_path_does_not_walk_url_file_parents(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as urls_root_dir:
            repo_path = Path(repo_dir)
            urls_root = Path(urls_root_dir)
            nested = urls_root / "nested"
            nested.mkdir()
            urls_path = nested / "urls.txt"
            urls_path.write_text("https://example.com\n", encoding="utf-8")
            (repo_path / ".env").write_text("MODMUX_TOKEN=repo-token\n", encoding="utf-8")
            (urls_root / ".env").write_text("MODMUX_TOKEN=wrong-token\n", encoding="utf-8")

            with patch.object(cli, "_REPO_ROOT", repo_path):
                previous_cwd = os.getcwd()
                os.chdir(repo_path)
                try:
                    resolved = cli._resolve_dotenv_path(anchors=[urls_path])
                finally:
                    os.chdir(previous_cwd)

        self.assertEqual(resolved, repo_path / ".env")

    def test_parse_provider_rejects_unknown(self) -> None:
        with self.assertRaises(Exception):
            cli._parse_provider("nope")

    def test_main_wraps_asyncio_run(self) -> None:
        def fake_run(coro: object) -> int:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return 7

        with patch.object(cli.asyncio, "run", side_effect=fake_run) as run_mock:
            with self.assertRaises(SystemExit) as exc:
                cli.main()

        self.assertEqual(exc.exception.code, 7)
        run_mock.assert_called_once()

    def test_module_entrypoint_calls_main(self) -> None:
        with patch("modmux.cli.main") as main_mock:
            runpy.run_module("modmux.__main__", run_name="__main__")

        main_mock.assert_called_once_with()
