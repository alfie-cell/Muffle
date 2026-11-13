import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_yt_config as byc  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "ubo-2026-09-20.txt")


class ParserTests(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, encoding="utf-8") as f:
            self.text = f.read()
        self.res = byc.parse(self.text)

    def test_snapshot_keys(self):
        self.assertEqual(sorted(self.res.keys), ["adPlacements", "adSlots", "playerAds"])

    def test_snapshot_needles_include_defaults(self):
        for n in byc.DEFAULT_NEEDLES:
            self.assertIn(n, self.res.needles)
        self.assertIn("/youtubei/v1/get_watch", self.res.needles)

    def test_snapshot_unparsed(self):
        # The Shorts fetch-response prune and the TV-only json-prune with array paths are
        # understood (json-prune) or deliberately left to the built-in handling.
        for line in self.res.unparsed:
            self.assertIn("json-prune-fetch-response", line, line)
        self.assertEqual(len(self.res.unparsed), 1)

    def test_network_rules_passed_through(self):
        joined = "\n".join(self.res.network)
        self.assertIn("*_ad_$media,domain=youtube.com,3p", joined)
        self.assertIn("||googlevideo.com/initplayback", joined)
        self.assertNotIn("replace=", joined)
        self.assertNotIn("+js(", joined)

    def test_new_set_constant_key_is_added(self):
        text = self.text + "\nwww.youtube.com##+js(set, ytInitialPlayerResponse.adBreakHeartbeatParams, undefined)\n"
        res = byc.parse(text)
        self.assertIn("adBreakHeartbeatParams", res.keys)

    def test_invalid_key_rejected(self):
        text = self.text + "\nwww.youtube.com##+js(set, ytInitialPlayerResponse.a-b, undefined)\n"
        res = byc.parse(text)
        self.assertEqual(sorted(res.keys), ["adPlacements", "adSlots", "playerAds"])

    def test_regex_url_arg_makes_no_needle(self):
        text = "www.youtube.com##+js(trusted-replace-xhr-response, '\"foo_bar\"', '\"no_ads\"', /some|regex/)\n"
        res = byc.parse(text)
        self.assertIn("foo_bar", res.keys)
        self.assertEqual(res.needles, byc.DEFAULT_NEEDLES)

    def test_plain_url_arg_becomes_needle(self):
        text = "www.youtube.com##+js(trusted-replace-fetch-response, '\"adSlots\"', '\"no_ads\"', /youtubei/v1/browse?)\n"
        res = byc.parse(text)
        self.assertIn("/youtubei/v1/browse?", res.needles)

    def test_replace_network_rule(self):
        text = '||youtube.com/youtubei/v1/reel/reel_item_watch?$xhr,1p,replace=/"adSlots"/"no_ads"/\n'
        res = byc.parse(text)
        self.assertIn("/youtubei/v1/reel/reel_item_watch", res.needles)
        self.assertEqual(res.network, [])

    def test_unknown_scriptlet_is_unparsed(self):
        text = "www.youtube.com##+js(brand-new-scriptlet, a, b)\n"
        res = byc.parse(text)
        self.assertEqual(res.unparsed, ["www.youtube.com##+js(brand-new-scriptlet, a, b)"])

    def test_non_youtube_rules_ignored(self):
        text = "viz.com##+js(set, show_youtube_preroll, false)\n||youtube.com/get_video$media,domain=games2rule.com\n"
        res = byc.parse(text)
        self.assertEqual(res.unparsed, [])
        self.assertEqual(res.network, [])

    def test_reserved_words_are_not_keys(self):
        text = "www.youtube.com##+js(json-prune, adPlacements important, , stack, /x/)\n"
        res = byc.parse(text)
        self.assertNotIn("important", res.keys)

    def test_caps(self):
        lines = "\n".join(f"www.youtube.com##+js(set, ytInitialPlayerResponse.key{i:02d}, undefined)" for i in range(40))
        res = byc.parse(lines)
        self.assertLessEqual(len(res.keys), byc.MAX_KEYS)

    def test_config_is_json(self):
        cfg = byc.config_dict(self.res, self.text)
        json.dumps(cfg)
        self.assertEqual(cfg["schema"], 1)


if __name__ == "__main__":
    unittest.main()
