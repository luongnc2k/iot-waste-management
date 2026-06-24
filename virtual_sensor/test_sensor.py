"""
Unit test cho BinState (virtual_sensor) — tập trung vào hành vi KHÓA NẮP.
Chạy: python -m unittest discover -s virtual_sensor -p "test_*.py"
Không cần MQTT broker (chỉ test logic mô phỏng thuần).
"""
import unittest

from sensor import BinState


class TestBinFillTrend(unittest.TestCase):
    def test_fill_increases_when_not_locked(self):
        b = BinState()
        start = b.fill_level
        for _ in range(10):
            b.tick()
        self.assertGreater(b.fill_level, start)   # có xu hướng tăng


class TestBinLock(unittest.TestCase):
    def test_fill_frozen_when_locked(self):
        b = BinState()
        b.locked = True
        b.tick()                       # tick để có giá trị ổn định sau khóa
        frozen = b.fill_level
        for _ in range(10):
            b.tick()
        self.assertEqual(b.fill_level, frozen)     # khóa → fill KHÔNG đổi

    def test_weight_does_not_grow_when_locked(self):
        b = BinState()
        b.locked = True
        b.tick()
        w0 = b.fill_level * 0.8
        for _ in range(10):
            b.tick()
        # weight chỉ dao động trong sai số cân (±~1kg), không tăng theo thời gian
        self.assertLess(abs(b.weight_kg - w0), 1.5)

    def test_set_lock_toggles(self):
        b = BinState()
        b.set_lock("on")
        self.assertTrue(b.locked)
        b.set_lock("off")
        self.assertFalse(b.locked)

    def test_resume_filling_after_unlock(self):
        b = BinState()
        b.set_lock("on")
        b.tick()
        frozen = b.fill_level
        b.set_lock("off")
        for _ in range(10):
            b.tick()
        self.assertGreater(b.fill_level, frozen)   # mở khóa → đầy lại

    def test_reset_clears_lock(self):
        b = BinState()
        b.locked = True
        b.reset_after_collection()
        self.assertFalse(b.locked)                 # thu gom xong → mở khóa


if __name__ == "__main__":
    unittest.main()
