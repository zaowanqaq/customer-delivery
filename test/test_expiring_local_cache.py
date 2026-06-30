# -*- coding: utf-8 -*-

# -*- coding: utf-8 -*-
# @Time    : 2024/6/2 10:35
# @Desc    :

import time
import unittest

from cache.local_cache import ExpiringLocalCache


class TestExpiringLocalCache(unittest.TestCase):

    def setUp(self):
        self.cache = ExpiringLocalCache(cron_interval=10)

    def test_set_and_get(self):
        self.cache.set('key', 'value', 10)
        self.assertEqual(self.cache.get('key'), 'value')

    def test_expired_key(self):
        self.cache.set('key', 'value', 1)
        time.sleep(2)  # wait for the key to expire
        self.assertIsNone(self.cache.get('key'))

    def test_clear(self):
        self.cache.set('key', 'value', 1)
        time.sleep(2)
        self.cache._clear()
        self.assertIsNone(self.cache.get('key'))

    def test_init_without_running_loop_does_not_create_pending_task(self):
        self.assertIsNone(self.cache._cron_task)

    def test_clear_multiple_expired_keys(self):
        self.cache.set('key1', 'value1', 1)
        self.cache.set('key2', 'value2', 1)
        time.sleep(2)
        self.cache._clear()

        self.assertEqual(self.cache.keys("*"), [])

    def tearDown(self):
        del self.cache


if __name__ == '__main__':
    unittest.main()
