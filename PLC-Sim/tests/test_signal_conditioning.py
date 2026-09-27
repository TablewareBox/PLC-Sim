"""沿用历史光电信号的时间、失效和未知语义回归。"""
import math
import unittest

from signal_conditioning import PhotoelectricSignal


class SignalContractTests(unittest.TestCase):
    def assert_reading(self, signal, time, raw, value, quality, **kwargs):
        output = signal.sample(time, raw, **kwargs)
        self.assertIs(output['value'], value)
        self.assertEqual(output['quality'], quality)
        return output

    def test_initial_reading_requires_full_stable_interval(self):
        signal = PhotoelectricSignal(.05)
        self.assert_reading(signal, 0, False, None, 'unknown')
        self.assert_reading(signal, .049, False, None, 'unknown')
        self.assert_reading(signal, .05, False, False, 'good')

    def test_short_positive_pulse_does_not_switch_output(self):
        signal = PhotoelectricSignal(.05)
        signal.sample(0, False); signal.sample(.05, False)
        for time, raw in ((.06, True), (.08, True), (.09, False), (.14, False)):
            self.assert_reading(signal, time, raw, False, 'good')

    def test_both_edges_wait_for_their_own_full_interval(self):
        signal = PhotoelectricSignal(.05)
        signal.sample(0, False); signal.sample(.05, False)
        self.assert_reading(signal, .10, True, False, 'good')
        self.assert_reading(signal, .149, True, False, 'good')
        self.assert_reading(signal, .15, True, True, 'good')
        self.assert_reading(signal, .20, False, True, 'good')
        self.assert_reading(signal, .249, False, True, 'good')
        self.assert_reading(signal, .25, False, False, 'good')

    def test_chatter_cannot_accumulate_noncontiguous_time(self):
        signal = PhotoelectricSignal(.05)
        signal.sample(0, False); signal.sample(.05, False)
        for time, raw in ((.06, True), (.10, False), (.11, True), (.15, False), (.16, True), (.209, True)):
            self.assert_reading(signal, time, raw, False, 'good')
        self.assert_reading(signal, .21, True, True, 'good')

    def test_disable_is_immediately_unknown_and_enable_requalifies(self):
        signal = PhotoelectricSignal(.05)
        signal.sample(0, True); signal.sample(.05, True)
        result = self.assert_reading(signal, .06, None, None, 'unknown', enabled=False)
        self.assertEqual(result['reason'], 'disabled')
        self.assert_reading(signal, .50, True, None, 'unknown')
        self.assert_reading(signal, .549, True, None, 'unknown')
        self.assert_reading(signal, .55, True, True, 'good')

    def test_invalid_sample_clears_old_output_and_pending_candidate(self):
        signal = PhotoelectricSignal(.05)
        signal.sample(0, False); signal.sample(.05, False)
        signal.sample(.06, True)
        output = self.assert_reading(signal, .10, None, None, 'unknown', valid=False)
        self.assertEqual(output['reason'], 'invalid_reading')
        self.assert_reading(signal, .11, True, None, 'unknown')
        self.assert_reading(signal, .159, True, None, 'unknown')
        self.assert_reading(signal, .16, True, True, 'good')

    def test_clock_reversal_cannot_preserve_a_stale_known_value(self):
        signal = PhotoelectricSignal(.05)
        signal.sample(1, True); signal.sample(1.05, True)
        output = self.assert_reading(signal, 0, False, None, 'unknown')
        self.assertEqual(output['reason'], 'time_reset')
        self.assert_reading(signal, .01, False, None, 'unknown')
        self.assert_reading(signal, .059, False, None, 'unknown')
        self.assert_reading(signal, .06, False, False, 'good')

    def test_repeated_timestamp_does_not_advance_debounce(self):
        signal = PhotoelectricSignal(.05)
        for _ in range(10):
            self.assert_reading(signal, 1, True, None, 'unknown')
        self.assert_reading(signal, 1.05, True, True, 'good')

    def test_nonfinite_time_and_nonboolean_measurement_rejected(self):
        for time in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                PhotoelectricSignal(.05).sample(time, True)
        for raw in (None, 0, 1, 'occupied'):
            with self.assertRaises(ValueError):
                PhotoelectricSignal(.05).sample(0, raw)
