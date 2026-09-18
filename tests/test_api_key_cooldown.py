# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import api_key_cooldown


class TestApiKeyCooldown(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / 'api_key_cooldowns.json'
        self.path_patch = patch.object(api_key_cooldown, 'STATE_PATH', self.state_path)
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_quarantine_persists_until_manual_restore(self) -> None:
        api_key_cooldown.quarantine('Tavily', 'bad-key', reason='Unauthorized: invalid API key')

        self.assertTrue(api_key_cooldown.is_cooled_down('Tavily', 'bad-key'))
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(state['Tavily']['bad-key']['status'], 'quarantined')
        self.assertNotIn('bad-key', state['Tavily']['bad-key']['reason'])

        self.assertEqual(api_key_cooldown.restore('Tavily', 'bad-key'), 1)
        self.assertFalse(api_key_cooldown.is_cooled_down('Tavily', 'bad-key'))

    def test_restore_also_clears_persisted_probe_failure(self) -> None:
        api_key_cooldown.increment_probe_failure('Tavily', 'bad-key')
        api_key_cooldown.quarantine('Tavily', 'bad-key', reason='deactivated')
        api_key_cooldown.increment_probe_failure('Tavily', 'bad-key')

        api_key_cooldown.restore('Tavily', 'bad-key')

        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertNotIn('Tavily', state.get('_probe_failures', {}))


if __name__ == '__main__':
    unittest.main()
