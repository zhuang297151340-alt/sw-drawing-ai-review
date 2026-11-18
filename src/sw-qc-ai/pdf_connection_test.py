# -*- coding: utf-8 -*-
"""PDF视觉模型连接测试：发送一张小图片并要求返回 JSON。"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from PIL import Image, ImageDraw

# 随插件部署的 Python 使用隔离式 python312._pth，默认不会把脚本目录加入
# sys.path。显式加入目录，确保同目录的 pdf_audit 包在开发/安装环境都可导入。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pdf_audit.providers import OpenAICompatibleVisionProvider, ProviderError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    try:
        provider = OpenAICompatibleVisionProvider(timeout=args.timeout)
        with tempfile.TemporaryDirectory(prefix="swqc_pdf_connection_") as tmp:
            image_path = os.path.join(tmp, "vision_probe.png")
            image = Image.new("RGB", (160, 90), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((18, 18, 142, 72), outline="black", width=4)
            draw.line((28, 45, 132, 45), fill="black", width=3)
            image.save(image_path)
            result = provider.call_json(
                "你是视觉连接测试器，只输出 JSON 对象。",
                "如果能够读取这张图片，请返回包含 ok=true 和 image_received=true 的 JSON。",
                [image_path],
            )
        if not isinstance(result, dict):
            raise ProviderError("模型返回值不是 JSON 对象。")
        print("@@CONNECTION_OK@@" + provider.description, flush=True)
        return 0
    except Exception as exc:
        print("@@CONNECTION_ERROR@@" + str(exc), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
