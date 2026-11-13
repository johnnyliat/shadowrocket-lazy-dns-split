"""GFWList 转换单元测试：冻结 AutoProxy -> 后缀条目的当前语义。

转换是有损的（审计已指出），本测试冻结"损在哪里"，防止未来改动悄悄
改变 P 层输入：@@ 白名单行跳过（不计入丢弃）；|http:// 形式因两次
sub 的顺序丢弃；*. 通配形式丢弃；IP 字面量与含下划线的行丢弃；大小写
归一、重复去重。
"""
import base64
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compile_supplement as cs

DECODED_FIXTURE = """[AutoProxy 0.2.9]
! comment line
||example.com^
||sub.example.org/path
@@||whitelisted.com^
|http://plain.com
https://secure.com:8080/x
*.wildgfw.com
||192.168.1.1^
||bad_domain.com^
||UPPER.COM^
example.net
||a.b.example.com^
||example.com^
"""


class TestGfwlistConversion(unittest.TestCase):
    def test_fixed_fixture(self):
        b64 = base64.b64encode(DECODED_FIXTURE.encode("utf-8")).decode("ascii")
        with mock.patch.object(cs, "fetch", return_value=b64):
            entries, dropped = cs.load_gfwlist()
        self.assertEqual(
            entries,
            [("suffix", "a.b.example.com"),
             ("suffix", "example.com"),
             ("suffix", "example.net"),
             ("suffix", "secure.com"),
             ("suffix", "sub.example.org"),
             ("suffix", "upper.com")])
        # 丢弃 4 条：|http:// 形式、*. 通配、IP 字面量、含下划线；
        # @@ 白名单与注释/段头不计入丢弃。
        self.assertEqual(dropped, 4)


if __name__ == "__main__":
    unittest.main()
