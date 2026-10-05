#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline tests for the registry download path: `--out` as a file, a folder, or a new folder.

    python3 tools/mod-registry/test_providers.py

Drives the real CurseForgeProvider.download with the network calls replaced, so it needs no key.
"""
import hashlib, io, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers  # noqa: E402

DATA = b"not really a jar"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def cf_download(out):
    prov = providers.CurseForgeProvider(api_key="test-key-not-real")
    meta = {"fileName": "example-mod-1.0.jar", "downloadUrl": "https://edge.forgecdn.net/x",
            "hashes": [{"algo": 1, "value": hashlib.sha1(DATA).hexdigest()}]}
    with mock.patch.object(prov, "_file", return_value=meta), \
         mock.patch("urllib.request.urlopen", return_value=FakeResponse(DATA)):
        return prov.download("123", "456", out)


class OutPath(unittest.TestCase):
    def test_a_file_path_is_used_as_given(self):
        with tempfile.TemporaryDirectory() as d:
            res = cf_download(os.path.join(d, "renamed.jar"))
            self.assertEqual(res["path"], os.path.join(d, "renamed.jar"))
            self.assertTrue(res["sha1_ok"])

    def test_an_existing_folder_keeps_the_files_own_name(self):
        with tempfile.TemporaryDirectory() as d:
            res = cf_download(d)  # used to raise IsADirectoryError
            self.assertEqual(res["path"], os.path.join(d, "example-mod-1.0.jar"))
            with open(res["path"], "rb") as f:
                self.assertEqual(f.read(), DATA)

    def test_a_new_folder_with_a_trailing_slash_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            res = cf_download(os.path.join(d, "mods") + "/")
            self.assertEqual(res["path"], os.path.join(d, "mods", "example-mod-1.0.jar"))

    def test_a_folder_with_no_file_name_says_what_to_do(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "pass a file path"):
                providers.write_download(d, None, DATA)

    def test_a_registry_name_cannot_escape_the_folder(self):
        with tempfile.TemporaryDirectory() as d:
            path = providers.write_download(d, "../../escape.jar", DATA)
            self.assertEqual(os.path.dirname(path), d)


if __name__ == "__main__":
    unittest.main(verbosity=1)
