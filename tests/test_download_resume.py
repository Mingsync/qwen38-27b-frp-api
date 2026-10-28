import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('download_model',Path(__file__).resolve().parents[1]/'download-model.py')
download=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(download)


class DownloadResumeTests(unittest.TestCase):
    def test_partial_download_resumes_only_for_original_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            download.ensure_download_identity(root,'test/model','a'*40)
            (root/'partial-shard').write_text('partial')
            download.ensure_download_identity(root,'test/model','a'*40)
            for repo,revision in [('other/model','a'*40),('test/model','b'*40)]:
                with self.assertRaises(ValueError):
                    download.ensure_download_identity(root,repo,revision)

    def test_unidentified_existing_directory_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'config.json').write_text('{}')
            with self.assertRaises(ValueError):
                download.ensure_download_identity(root,'test/model','a'*40)

    def test_matching_legacy_manifest_can_be_upgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'deployment-manifest.json').write_text(json.dumps({'repo':'test/model','revision':'a'*40}))
            download.ensure_download_identity(root,'test/model','a'*40)
            self.assertTrue((root/'.deployment-download.json').exists())
