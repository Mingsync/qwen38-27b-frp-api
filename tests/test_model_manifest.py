import json
from pathlib import Path
import tempfile
import unittest

from validate_model import sha256, validate


class ModelManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo, self.revision = 'test/model', 'a' * 40
        (self.root/'config.json').write_text('{"architectures":["fixture"]}')
        (self.root/'tokenizer.json').write_text('fixture-tokenizer')
        (self.root/'model-1.safetensors').write_bytes(b'fixture-weights')
        (self.root/'model.safetensors.index.json').write_text(json.dumps(
            {'weight_map': {'weight': 'model-1.safetensors'}}))
        self.manifest = {
            'schema_version': 2, 'repo': self.repo, 'revision': self.revision,
            'config_sha256': sha256(self.root/'config.json'),
            'index_sha256': sha256(self.root/'model.safetensors.index.json'),
            'files': {p.name: {'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in self.root.iterdir()},
        }
        self.save()

    def save(self):
        (self.root/'deployment-manifest.json').write_text(json.dumps(self.manifest))

    def test_matching_revision_and_full_integrity(self):
        validate(self.root, self.repo, self.revision, full_hash=True)

    def test_wrong_repo_or_revision_is_rejected(self):
        for repo, revision in [('other/model', self.revision), (self.repo, 'b' * 40)]:
            with self.assertRaisesRegex(ValueError, 'repo/revision'):
                validate(self.root, repo, revision)

    def test_legacy_manifest_requires_explicit_upgrade(self):
        self.manifest.pop('schema_version'); self.save()
        with self.assertRaisesRegex(ValueError, 'Manifest v2'):
            validate(self.root, self.repo, self.revision)

    def test_config_and_tokenizer_tampering_is_rejected(self):
        for name in ['config.json', 'tokenizer.json']:
            original = (self.root/name).read_bytes()
            (self.root/name).write_bytes(b'x' * len(original))
            with self.assertRaises(ValueError):
                validate(self.root, self.repo, self.revision)
            (self.root/name).write_bytes(original)

    def test_missing_and_truncated_weights_are_rejected(self):
        path = self.root/'model-1.safetensors'
        path.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'size mismatch'):
            validate(self.root, self.repo, self.revision)
        path.unlink()
        with self.assertRaises(ValueError):
            validate(self.root, self.repo, self.revision)

    def test_full_scan_detects_same_size_weight_corruption(self):
        path = self.root/'model-1.safetensors'
        path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            validate(self.root, self.repo, self.revision, full_hash=True)
