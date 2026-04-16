from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import dashboard_api


class DashboardWebStaticAssetResolutionTest(unittest.TestCase):
    def test_resolves_root_route_and_asset_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_root = Path(tmp_dir)
            assets_dir = dist_root / "assets"
            assets_dir.mkdir()
            index_file = dist_root / "index.html"
            script_file = assets_dir / "app.js"
            index_file.write_text("<html></html>", encoding="utf-8")
            script_file.write_text("console.log('ok')", encoding="utf-8")

            self.assertEqual(dashboard_api._resolve_dashboard_web_asset_path("/", dist_root), index_file.resolve())
            self.assertEqual(dashboard_api._resolve_dashboard_web_asset_path("/signals", dist_root), index_file.resolve())
            self.assertEqual(
                dashboard_api._resolve_dashboard_web_asset_path("/assets/app.js", dist_root),
                script_file.resolve(),
            )

    def test_blocks_parent_traversal_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_root = Path(tmp_dir)
            index_file = dist_root / "index.html"
            index_file.write_text("<html></html>", encoding="utf-8")

            self.assertIsNone(dashboard_api._resolve_dashboard_web_asset_path("/../README.md", dist_root))
            self.assertIsNone(dashboard_api._resolve_dashboard_web_asset_path("/assets/missing.js", dist_root))

    def test_returns_none_when_build_output_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dist_root = Path(tmp_dir) / "missing"
            self.assertIsNone(dashboard_api._resolve_dashboard_web_asset_path("/", dist_root))


class DashboardWebContentTypeTest(unittest.TestCase):
    def test_adds_charset_for_text_and_svg_assets(self) -> None:
        self.assertEqual(
            dashboard_api._dashboard_web_content_type(Path("index.html")),
            "text/html; charset=utf-8",
        )
        self.assertEqual(
            dashboard_api._dashboard_web_content_type(Path("app.js")),
            "text/javascript; charset=utf-8",
        )
        self.assertEqual(
            dashboard_api._dashboard_web_content_type(Path("icon.svg")),
            "image/svg+xml; charset=utf-8",
        )


if __name__ == "__main__":
    unittest.main()
