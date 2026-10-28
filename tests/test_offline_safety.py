import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'offline'))
import prepare_image
import export_caches
import restore_bundle
import verify_offline
from validate_model import sha256


class OfflineSafetyTests(unittest.TestCase):
    def bundle(self,directory,accepted=True):
        root=Path(directory)
        (root/'inference-image.tar').write_bytes(b'fixture-image')
        (root/'runtime-cache.tar.gz').write_bytes(b'fixture-cache')
        manifest={'image_id':'sha256:'+'a'*64,
            'image_sha256':sha256(root/'inference-image.tar'),
            'cache_sha256':sha256(root/'runtime-cache.tar.gz'),
            'gpu_offline_acceptance':{'status':'passed' if accepted else 'failed','image_id':'sha256:'+'a'*64}}
        (root/'bundle.json').write_text(json.dumps(manifest))
        return root,manifest

    def test_floating_base_image_is_rejected_before_build(self):
        with patch('sys.argv',['prepare','--base-image','vendor:latest','--output','unused']), \
                patch.object(prepare_image.subprocess,'run') as docker:
            with self.assertRaises(SystemExit): prepare_image.main()
            docker.assert_not_called()

    def test_corrupt_archive_is_rejected_before_any_docker_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root,_=self.bundle(directory)
            (root/'inference-image.tar').write_bytes(b'corrupt')
            with patch('sys.argv',['restore','--bundle',directory]), \
                    patch.object(restore_bundle.subprocess,'run') as docker:
                with self.assertRaisesRegex(RuntimeError,'checksum'): restore_bundle.main()
                docker.assert_not_called()

    def test_export_requires_successful_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle(directory,accepted=False)
            with patch('sys.argv',['export','--bundle',directory]), \
                    patch.object(export_caches.subprocess,'run') as docker:
                with self.assertRaisesRegex(RuntimeError,'acceptance'): export_caches.main()
                docker.assert_not_called()

    def test_image_mismatch_never_starts_compose(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle(directory)
            with patch('sys.argv',['verify','--image','sha256:'+'b'*64,'--bundle',directory]), \
                    patch.object(verify_offline.subprocess,'check_output',return_value='sha256:'+'b'*64), \
                    patch.object(verify_offline.subprocess,'run') as docker:
                with self.assertRaises(SystemExit): verify_offline.main()
                docker.assert_not_called()

    def test_failed_acceptance_invalidates_old_proof_and_cleans_only_test_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root,manifest=self.bundle(directory)
            with patch('sys.argv',['verify','--image',manifest['image_id'],'--bundle',directory]), \
                    patch.object(verify_offline.subprocess,'check_output',return_value=manifest['image_id']), \
                    patch.object(verify_offline.subprocess,'run',side_effect=[
                        subprocess.CalledProcessError(1,['fixture']),None,None]) as docker:
                with self.assertRaises(subprocess.CalledProcessError): verify_offline.main()
                self.assertEqual(json.loads((root/'bundle.json').read_text())['gpu_offline_acceptance']['status'],'failed')
                self.assertEqual(docker.call_args_list[-1].args[0][-1],'down')
                self.assertIn('qwen38-offline-check',docker.call_args_list[-1].args[0])
